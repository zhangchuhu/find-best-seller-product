"""Three-stage, checkpointed best-seller research workflow."""

from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import uuid

from scripts.ark_vision import ArkVisionClient, VisualProfile
from scripts.autocomplete import resolve_autocomplete
from scripts.candidates import eligible_identities, merge_observations, select_results
from scripts.checkpoint import CheckpointStore
from scripts.lark_base import LarkBaseClient
from scripts.metrics import parse_count, parse_rating
from scripts.models import Observation, Task, VerifiedCandidate
from scripts.platforms import Platform, canonicalize_url, product_identity, route_platform
from scripts.query_terms import MARKETPLACE_LANGUAGES, contains_color_or_size


NEXT_STEP = (
    "Use the selected Chrome session to collect evidence.json for the "
    "manifest queries, then run validate-evidence --run-dir <RUN_DIR> "
    "--input <EVIDENCE_JSON>."
)
_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)
MAX_EVIDENCE_JSON_BYTES = 5 * 1024 * 1024
_DETAIL_KEYS = {
    "identity", "status", "reason", "detail_url", "product_id", "title",
    "category", "sold_display", "reviews_display", "rating_display",
    "match_level", "visual_features",
}
_REJECTION_REASONS = {
    "category_mismatch",
    "imagery_ambiguous_or_inaccessible",
    "metric_missing_or_ambiguous",
    "threshold_failure",
    "identity_changed",
    "detail_inaccessible",
}


class WorkflowError(RuntimeError):
    """A bounded error safe to show at the command boundary."""


def _error(category: str) -> WorkflowError:
    safe = "".join(character for character in category if character.isalnum() or character in " -_.")
    return WorkflowError((safe.strip() or "workflow failed")[:240])


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-standard number")


def _load_json(path: Path) -> object:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise OSError
        if status.st_size > MAX_EVIDENCE_JSON_BYTES:
            raise _error("evidence JSON exceeds the size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_EVIDENCE_JSON_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_EVIDENCE_JSON_BYTES:
                raise _error("evidence JSON exceeds the size limit")
        return json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except WorkflowError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise _error("evidence JSON is invalid") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    try:
        parent = path.parent
        if parent.is_symlink() or not parent.is_dir():
            raise OSError
        if path.exists() or path.is_symlink():
            status = path.lstat()
            if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
                raise OSError
        encoded = (
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode("utf-8")
        temporary = parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            try:
                os.fsync(directory)
            except OSError:
                pass
        finally:
            os.close(directory)
    except (OSError, TypeError, ValueError, UnicodeError):
        try:
            if "temporary" in locals() and temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise _error("local JSON file could not be saved") from None


def _task_dict(task: Task) -> dict[str, object]:
    return {
        "record_id": task.record_id,
        "sku": task.sku,
        "image_token": task.image_token,
        "image_name": task.image_name,
        "platform": task.platform.value,
        "min_sold": task.min_sold,
        "min_reviews": task.min_reviews,
        "min_rating": task.min_rating,
        "result_limit": task.result_limit,
    }


def _observation_dict(value: Observation, platform: Platform) -> dict[str, object]:
    return {
        "query": value.query,
        "rank": value.rank,
        "is_ad": value.is_ad,
        "title": value.title,
        "url": canonicalize_url(platform, value.url),
        "product_id": value.product_id,
        "thumbnail_url": value.thumbnail_url,
    }


def _candidate_dict(value: VerifiedCandidate) -> dict[str, object]:
    return {
        "identity": value.identity,
        "platform": value.platform.value,
        "title": value.title,
        "canonical_url": value.canonical_url,
        "query_hits": sorted(value.query_hits),
        "earliest_organic_rank": value.earliest_organic_rank,
        "earliest_ad_rank": value.earliest_ad_rank,
        "sold_display": value.sold_display,
        "sold_value": value.sold_value,
        "reviews_display": value.reviews_display,
        "reviews_value": value.reviews_value,
        "rating_display": value.rating_display,
        "rating_value": value.rating_value,
        "match_level": value.match_level,
        "visual_features": list(value.visual_features),
    }


def _exact(value: object, keys: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise _error(f"{label} fields are invalid")
    return value


def _store(root: Path) -> CheckpointStore:
    configured = os.environ.get("ARK_API_KEY")
    forbidden = (configured,) if isinstance(configured, str) and configured else ()
    return CheckpointStore(root, forbidden_values=forbidden)


def _run_parts(run_dir: Path, *, verify_checkpoint: bool = True) -> tuple[Path, str, Path]:
    supplied = Path(run_dir).absolute()
    if supplied.is_symlink():
        raise _error("run directory is invalid")
    path = supplied.resolve()
    record_id = path.name
    try:
        if not path.is_dir():
            raise ValueError
        if verify_checkpoint:
            store = _store(path.parent)
            store.load(record_id)
    except Exception:
        raise _error("run directory is invalid") from None
    return path.parent, record_id, path


def _prepared_payload(
    checkpoint: Mapping[str, object], record_id: str, run_dir: Path
) -> dict[str, object]:
    try:
        stages = checkpoint["stages"]
        if not isinstance(stages, dict):
            raise ValueError
        payload = _exact(
            stages["prepared"],
            {"task", "source_image", "visual_profile", "query_seeds"},
            "prepared checkpoint",
        )
        task = Task.from_dict(payload["task"])
        if task.record_id != record_id:
            raise ValueError
        source = _exact(payload["source_image"], {"path", "name"}, "source image")
        image_path = Path(source["path"])
        if (
            not isinstance(source["path"], str)
            or not image_path.is_absolute()
            or source["name"] != image_path.name
            or image_path.parent != run_dir
            or image_path.is_symlink()
            or not image_path.is_file()
            or image_path.stat().st_size <= 0
        ):
            raise ValueError
        profile = VisualProfile.from_dict(payload["visual_profile"], task.platform)
        seeds = payload["query_seeds"]
        if not isinstance(seeds, list) or tuple(seeds) != profile.query_seeds:
            raise ValueError
        return payload
    except (KeyError, TypeError, ValueError, WorkflowError):
        raise _error("prepared checkpoint is invalid") from None


def _manifest_payload(
    prepared: Mapping[str, object], queries: tuple[str, str, str] | None = None,
) -> dict[str, object]:
    """Return the only manifest layouts accepted by this workflow.

    The prepared checkpoint owns all four base fields. Resolution appends only
    its three persisted, verbatim platform queries.
    """
    manifest = dict(prepared)
    if queries is not None:
        manifest["queries"] = list(queries)
    return manifest


def _direct_resolution_payload(
    prepared: Mapping[str, object],
) -> tuple[dict[str, object], tuple[str, str, str]]:
    """Build the sole direct resolution accepted for newly prepared runs."""
    task = Task.from_dict(prepared["task"])
    profile = VisualProfile.from_dict(prepared["visual_profile"], task.platform)
    queries = profile.query_seeds
    return {"source": "ark_seeds", "queries": list(queries)}, queries


def _queries_resolved_payload(
    checkpoint: Mapping[str, object], record_id: str, run_dir: Path,
) -> tuple[dict[str, object], tuple[str, str, str]]:
    """Validate either a direct Ark resolution or a legacy autocomplete one."""
    try:
        prepared = _prepared_payload(checkpoint, record_id, run_dir)
        stages = checkpoint["stages"]
        if not isinstance(stages, dict):
            raise ValueError
        task = Task.from_dict(prepared["task"])
        profile = VisualProfile.from_dict(prepared["visual_profile"], task.platform)
        raw_resolved = stages["queries_resolved"]
        if isinstance(raw_resolved, dict) and set(raw_resolved) == {"source", "queries"}:
            resolved = _exact(raw_resolved, {"source", "queries"}, "resolved queries checkpoint")
            queries = resolved["queries"]
            if (
                resolved["source"] != "ark_seeds"
                or not isinstance(queries, list)
                or tuple(queries) != profile.query_seeds
            ):
                raise ValueError
            return resolved, profile.query_seeds
        resolved = _exact(
            raw_resolved,
            {"autocomplete_evidence", "queries"},
            "resolved queries checkpoint",
        )
        parsed = resolve_autocomplete(
            resolved["autocomplete_evidence"],
            record_id=record_id,
            platform=task.platform,
            profile=profile,
        )
        queries = resolved["queries"]
        if (
            not isinstance(queries, list)
            or len(queries) != 3
            or tuple(queries) != parsed.queries
            or resolved["autocomplete_evidence"] != parsed.evidence
        ):
            raise ValueError
        return resolved, parsed.queries
    except (KeyError, TypeError, ValueError, WorkflowError):
        raise _error("resolved queries checkpoint is invalid") from None


def _autocomplete_provenance(
    resolved: Mapping[str, object], queries: tuple[str, str, str],
) -> dict[str, object]:
    """Copy only the checkpoint-owned resolution facts into later evidence."""
    return {
        "autocomplete_evidence": resolved["autocomplete_evidence"],
        "queries": list(queries),
    }


def _resolution_provenance(
    resolved: Mapping[str, object], queries: tuple[str, str, str],
) -> tuple[str, dict[str, object]]:
    """Return the durable provenance layout for direct and legacy resolutions."""
    if set(resolved) == {"source", "queries"}:
        return "query_provenance", {"source": "ark_seeds", "queries": list(queries)}
    return "autocomplete_provenance", _autocomplete_provenance(resolved, queries)


def _canonical_evidence_digest(payload: Mapping[str, object]) -> str:
    """Return a deterministic integrity digest for replayed local evidence."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_resolution_provenance(
    evidence: Mapping[str, object],
    checkpoint: Mapping[str, object],
    record_id: str,
    run_dir: Path,
) -> tuple[str, str, str]:
    """Bind validated product evidence to the durable resolution checkpoint."""
    resolved, queries = _queries_resolved_payload(checkpoint, record_id, run_dir)
    provenance_key, expected = _resolution_provenance(resolved, queries)
    if provenance_key == "query_provenance":
        provenance = _exact(
            evidence[provenance_key], {"source", "queries"}, "evidence query provenance",
        )
    else:
        provenance = _exact(
            evidence[provenance_key], {"autocomplete_evidence", "queries"},
            "evidence autocomplete provenance",
        )
    if provenance != expected:
        raise ValueError
    return queries


def _legacy_finalized_summary(
    checkpoint: Mapping[str, object], run_dir: Path,
) -> str | None:
    """Read a completed v1 run without interpreting its obsolete Ark profile."""
    if checkpoint.get("version") != 1:
        return None
    try:
        if checkpoint.get("stage") != "finalized":
            raise ValueError
        stages = checkpoint["stages"]
        if not isinstance(stages, dict) or set(stages) != {
            "prepared", "evidence_validated", "finalized",
        }:
            raise ValueError
        final = _exact(stages["finalized"], {"result_count", "completed"}, "legacy finalized checkpoint")
        count = final["result_count"]
        completed = final["completed"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError
        if (
            not isinstance(completed, list)
            or len(completed) != count
            or len(completed) != len(set(completed))
            or any(not isinstance(item, str) or not item or len(item) > 2048 for item in completed)
        ):
            raise ValueError
        output = _exact(
            _load_json(run_dir / "final-results.json"),
            {"task_record_id", "platform", "result_count", "results"},
            "legacy final results",
        )
        output_count = output["result_count"]
        if (
            output["task_record_id"] != checkpoint["record_id"]
            or not isinstance(output["platform"], str)
            or type(output_count) is not int
            or output_count < 0
            or output_count != count
            or not isinstance(output["results"], list)
            or len(output["results"]) != output_count
        ):
            raise ValueError
        platform = Platform(output["platform"])
        result_by_url: dict[str, VerifiedCandidate] = {}
        result_identities: set[str] = set()
        for raw_candidate in output["results"]:
            candidate = VerifiedCandidate.from_dict(raw_candidate)
            if candidate.platform is not platform:
                raise ValueError
            canonical_url = canonicalize_url(platform, candidate.canonical_url)
            if canonical_url != candidate.canonical_url:
                raise ValueError
            if candidate.identity != product_identity(platform, canonical_url):
                raise ValueError
            if candidate.identity in result_identities or canonical_url in result_by_url:
                raise ValueError
            result_identities.add(candidate.identity)
            result_by_url[canonical_url] = candidate
        completed_by_url: dict[str, str] = {}
        for business_key in completed:
            parsed_key = json.loads(business_key, object_pairs_hook=_strict_object, parse_constant=_reject_constant)
            if (
                not isinstance(parsed_key, list)
                or len(parsed_key) != 3
                or any(not isinstance(item, str) or not item.strip() for item in parsed_key)
                or len(parsed_key[0]) > 256
                or parsed_key[1] != platform.value
            ):
                raise ValueError
            key_url = canonicalize_url(platform, parsed_key[2])
            if key_url != parsed_key[2] or key_url not in result_by_url or key_url in completed_by_url:
                raise ValueError
            completed_by_url[key_url] = parsed_key[0]
        if len(completed_by_url) != count or tuple(completed_by_url) != tuple(result_by_url):
            raise ValueError
    except (KeyError, TypeError, ValueError, WorkflowError):
        raise _error("legacy finalized checkpoint is invalid") from None
    return f"Already finalized {count} result(s): {run_dir / 'final-results.json'}"


def _resolve_queries_locked(run_dir: Path, input_path: Path) -> str:
    root, record_id, absolute_run = _run_parts(run_dir)
    store = _store(root)
    try:
        checkpoint = store.load(record_id)
        if checkpoint is None:
            raise ValueError
        if checkpoint.get("version") != 2:
            raise _error("legacy checkpoint requires a restarted analysis")
        if checkpoint["stage"] == "finalized":
            raise _error("finalized queries cannot be replaced")
        prepared = _prepared_payload(checkpoint, record_id, absolute_run)
        task = Task.from_dict(prepared["task"])
        profile = VisualProfile.from_dict(prepared["visual_profile"], task.platform)
    except WorkflowError:
        raise
    except Exception:
        raise _error("prepared checkpoint is required") from None

    raw = _load_json(Path(input_path))
    try:
        parsed = resolve_autocomplete(
            raw, record_id=record_id, platform=task.platform, profile=profile,
        )
        payload = {
            "autocomplete_evidence": parsed.evidence,
            "queries": list(parsed.queries),
        }
        if checkpoint["stage"] != "prepared":
            existing, existing_queries = _queries_resolved_payload(
                checkpoint, record_id, absolute_run,
            )
            if existing != payload or existing_queries != parsed.queries:
                raise _error("autocomplete evidence cannot be replaced")
        else:
            store.save_stage(record_id, "queries_resolved", payload)
        # Checkpoint first: a crash here is repaired by an identical retry, and
        # no remote operation has occurred.
        _write_json(absolute_run / "manifest.json", _manifest_payload(prepared, parsed.queries))
    except WorkflowError:
        raise
    except Exception:
        raise _error("autocomplete evidence is invalid") from None
    return f"Resolved {record_id}: {', '.join(parsed.queries)}"


def resolve_queries(run_dir: Path, input_path: Path) -> str:
    _root, _record_id, absolute_run = _run_parts(Path(run_dir), verify_checkpoint=False)
    with _record_lock(absolute_run):
        return _resolve_queries_locked(absolute_run, Path(input_path))


def prepare(
    record_id=None,
    work_root=Path(".work"),
    dry_run=False,
    restart_analysis=False,
    lark_client=None,
    ark_client=None,
):
    if record_id is not None and (not isinstance(record_id, str) or not record_id.strip()):
        raise _error("record id is invalid")
    if type(dry_run) is not bool or type(restart_analysis) is not bool:
        raise _error("workflow flags are invalid")
    lark = lark_client or LarkBaseClient()
    try:
        lark.validate_base_contracts()
        tasks, failures = lark.list_pending_tasks(record_id=record_id)
    except Exception:
        raise _error("Lark task read failed") from None
    if failures:
        failure = failures[0]
        failure_record = getattr(failure, "record_id", "unknown")
        failure_message = getattr(failure, "message", "validation failed")
        if (
            record_id is not None
            and getattr(failure, "terminal", False) is True
            and dry_run is False
        ):
            try:
                lark.set_task_status(failure_record, "失败", dry_run=False)
            except Exception:
                pass
        raise _error(f"pending task {failure_record} - {failure_message}")
    if not tasks:
        raise _error("no valid pending task")
    if record_id is not None:
        requested = record_id.strip()
        if len(tasks) != 1 or tasks[0].record_id != requested:
            raise _error("selected record scope does not match")
        selected = tasks[0]
    else:
        selected = sorted(tasks, key=lambda item: item.record_id)[0]
    root = Path(work_root).resolve()
    run_dir = root / selected.record_id
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise _error("run directory could not be initialized") from None
    with _record_lock(run_dir):
        return _prepare_locked(
            selected, root, restart_analysis, lark, ark_client,
        )


def _prepare_locked(selected, root, restart_analysis, lark, ark_client):
    """Persist a preparation while holding the record's workflow lock."""
    store = _store(root)
    try:
        checkpoint = store.load(selected.record_id)
    except Exception:
        raise _error("checkpoint could not be loaded") from None
    if checkpoint is not None and restart_analysis:
        if checkpoint["stage"] == "queries_resolved":
            raise _error("restart-analysis cannot replace direct queries")
        if checkpoint["stage"] != "prepared":
            raise _error("restart-analysis cannot invalidate validated evidence")
    if checkpoint is not None and not restart_analysis:
        payload = _prepared_payload(checkpoint, selected.record_id, root / selected.record_id)
        if payload["task"] != _task_dict(selected):
            raise _error("prepared task no longer matches Lark")
        manifest_path = root / selected.record_id / "manifest.json"
        queries = None
        if checkpoint.get("version") == 2 and checkpoint["stage"] == "prepared":
            try:
                resolution, queries = _direct_resolution_payload(payload)
                store.save_stage(selected.record_id, "queries_resolved", resolution)
                checkpoint = store.load(selected.record_id)
                if checkpoint is None:
                    raise ValueError
            except Exception:
                raise _error("resolved queries checkpoint could not be saved") from None
        if checkpoint.get("version") == 2 and checkpoint["stage"] != "prepared":
            _resolved, queries = _queries_resolved_payload(
                checkpoint, selected.record_id, root / selected.record_id,
            )
        _write_json(manifest_path, _manifest_payload(payload, queries))
        return f"Prepared {selected.record_id}; manifest: {manifest_path.absolute()}"

    run_dir = root / selected.record_id
    try:
        returned_path = Path(lark.download_source_image(selected, run_dir))
        if returned_path.is_symlink():
            raise ValueError
        image_path = returned_path.resolve(strict=True)
        resolved_run = run_dir.resolve(strict=True)
        if (
            not image_path.is_relative_to(resolved_run)
            or not image_path.is_file()
            or image_path.stat().st_size <= 0
        ):
            raise ValueError
    except Exception:
        raise _error("source image download failed") from None
    try:
        ark = ark_client or ArkVisionClient()
        visual = ark.analyze(image_path, selected.platform)
        if not isinstance(visual, VisualProfile):
            raise TypeError
    except Exception:
        raise _error("Ark image analysis failed") from None
    payload = {
        "task": _task_dict(selected),
        "source_image": {"path": str(image_path), "name": image_path.name},
        "visual_profile": visual.to_dict(),
        "query_seeds": list(visual.query_seeds),
    }
    try:
        store.save_stage(selected.record_id, "prepared", payload)
        resolution, queries = _direct_resolution_payload(payload)
        store.save_stage(selected.record_id, "queries_resolved", resolution)
    except Exception:
        raise _error("prepared checkpoint could not be saved") from None
    manifest_path = run_dir / "manifest.json"
    _write_json(manifest_path, _manifest_payload(payload, queries))
    return f"Prepared {selected.record_id}; manifest: {manifest_path.absolute()}"


def _detail_candidate(
    raw: object,
    merged: Mapping[str, object],
    platform: Platform,
    task: Task,
    profile: VisualProfile,
) -> tuple[VerifiedCandidate | None, dict[str, object]]:
    detail = _exact(raw, _DETAIL_KEYS, "detail")
    identity = detail["identity"]
    if not isinstance(identity, str) or identity not in merged:
        raise _error("detail identity is not linked to observations")
    observation = merged[identity]

    status_value = detail["status"]
    if status_value not in {"qualified", "rejected"}:
        raise _error("detail status is invalid")
    reason = detail["reason"]
    if status_value == "qualified":
        if reason is not None:
            raise _error("qualified detail cannot have a rejection reason")
    elif not isinstance(reason, str) or reason not in _REJECTION_REASONS:
        raise _error("detail rejection reason is invalid")

    detail_url = detail["detail_url"]
    explicit_id = detail["product_id"]
    normalized_url: str | None = None
    identity_changed = False
    if detail_url is not None:
        if not isinstance(detail_url, str) or not detail_url.strip():
            raise _error("detail URL is invalid")
        try:
            normalized_url = canonicalize_url(platform, detail_url)
            url_identity = product_identity(platform, normalized_url)
            bound_identity = product_identity(platform, normalized_url, explicit_id) if explicit_id is not None else url_identity
        except (TypeError, ValueError):
            raise _error("detail product identity is invalid") from None
        fallback_prefix = f"{platform.value}:https://"
        if explicit_id is not None and not url_identity.startswith(fallback_prefix) and bound_identity != url_identity:
            raise _error("detail product ID conflicts with URL")
        identity_changed = bound_identity != identity
        if identity_changed and not (
            status_value == "rejected" and reason == "identity_changed"
        ):
            raise _error("detail identity changed from search evidence")
    elif explicit_id is not None:
        raise _error("detail product ID requires a detail URL")

    title = detail["title"]
    category = detail["category"]
    for value, label in ((title, "title"), (category, "category")):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise _error(f"detail {label} is invalid")
    title = title.strip() if isinstance(title, str) else None
    category = category.strip() if isinstance(category, str) else None

    sold = parse_count(detail["sold_display"])
    reviews = parse_count(detail["reviews_display"])
    rating = parse_rating(detail["rating_display"])
    required_metrics = (
        (("sold", sold, task.min_sold),)
        if platform is Platform.MERCADO_MX
        else (
            ("reviews", reviews, task.min_reviews),
            ("rating", rating, task.min_rating),
        )
    )
    features = detail["visual_features"]

    normalized = {
        "identity": identity,
        "status": status_value,
        "reason": reason,
        "detail_url": normalized_url,
        "product_id": explicit_id.strip() if isinstance(explicit_id, str) else None,
        "title": title,
        "category": category,
        "sold_display": detail["sold_display"],
        "reviews_display": detail["reviews_display"],
        "rating_display": detail["rating_display"],
        "match_level": detail["match_level"],
        "visual_features": features,
    }

    normalize_category = lambda value: " ".join(value.casefold().split())
    accepted_categories = {
        normalize_category(profile.category), normalize_category(profile.subtype)
    }
    category_matches = (
        category is not None and normalize_category(category) in accepted_categories
    )

    if status_value == "rejected":
        if detail["match_level"] is not None or detail["visual_features"] is not None:
            raise _error("rejected detail cannot contain match evidence")
        if reason in {"category_mismatch", "metric_missing_or_ambiguous", "threshold_failure"}:
            if normalized_url is None or title is None or category is None:
                raise _error("detail rejection reason requires visible detail identity title and category")
        if reason == "imagery_ambiguous_or_inaccessible" and normalized_url is None:
            raise _error("imagery rejection requires a detail URL")
        if reason == "threshold_failure" and any(value is None for _, value, _ in required_metrics):
            raise _error("threshold rejection requires unambiguous metrics")
        if reason == "threshold_failure" and not any(
            value is not None and value < threshold
            for _, value, threshold in required_metrics
        ):
            raise _error("threshold rejection requires a failed task threshold")
        if reason == "metric_missing_or_ambiguous" and all(
            value is not None for _, value, _ in required_metrics
        ):
            raise _error("metric rejection must identify a missing metric")
        if reason == "category_mismatch" and category_matches:
            raise _error("category rejection requires a mismatched source category")
        if reason not in {"category_mismatch", "detail_inaccessible"} and category is not None and not category_matches:
            raise _error("detail rejection reason conflicts with the visible category")
        if reason == "identity_changed" and normalized_url is None:
            raise _error("identity rejection requires detail identity evidence")
        if reason == "identity_changed" and not identity_changed:
            raise _error("identity rejection requires a changed identity")
        if reason == "detail_inaccessible" and any(
            value is not None
            for value in (
                normalized_url, explicit_id, title, category,
                detail["sold_display"], detail["reviews_display"],
                detail["rating_display"], detail["match_level"],
                detail["visual_features"],
            )
        ):
            raise _error("inaccessible detail cannot claim visible detail evidence")
        return None, normalized

    if normalized_url is None or title is None or category is None:
        raise _error("qualified detail identity title and category are required")
    if not category_matches:
        raise _error("qualified detail category does not match the source profile")
    if any(value is None for _, value, _ in required_metrics):
        raise _error("qualified detail metrics are missing or ambiguous")
    if any(
        value is not None and value < threshold
        for _, value, threshold in required_metrics
    ):
        raise _error("qualified detail is below a task threshold")
    if isinstance(features, (str, bytes)) or not isinstance(features, list):
        raise _error("detail visual features are invalid")
    try:
        if any(
            contains_color_or_size(feature, language, profile.color)
            for feature in features
            for language in MARKETPLACE_LANGUAGES
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise _error("detail visual features are invalid") from None
    try:
        candidate = VerifiedCandidate(
            identity=identity,
            platform=platform,
            title=title,
            canonical_url=normalized_url,
            query_hits=observation.query_hits,
            earliest_organic_rank=observation.earliest_organic_rank,
            earliest_ad_rank=observation.earliest_ad_rank,
            sold_display=detail["sold_display"],
            sold_value=sold,
            reviews_display=detail["reviews_display"],
            reviews_value=reviews,
            rating_display=detail["rating_display"],
            rating_value=rating,
            match_level=detail["match_level"],
            visual_features=tuple(features),
        )
    except (TypeError, ValueError):
        raise _error("detail candidate is invalid") from None
    normalized["sold_display"] = candidate.sold_display
    normalized["reviews_display"] = candidate.reviews_display
    normalized["rating_display"] = candidate.rating_display
    normalized["match_level"] = candidate.match_level
    normalized["visual_features"] = list(candidate.visual_features)
    return candidate, normalized


def _recurring_pool(merged: Mapping[str, object], recurring: set[str]) -> list[str]:
    def order(identity: str) -> tuple[object, ...]:
        value = merged[identity]
        organic = value.earliest_organic_rank
        sponsored = value.earliest_ad_rank
        return (
            -len(value.query_hits),
            organic is None,
            organic if organic is not None else sponsored,
            identity,
        )
    return sorted(recurring, key=order)


def _validation_timestamp(clock) -> str:
    try:
        value = clock()
    except Exception:
        raise _error("validation clock failed") from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _error("validation clock is invalid")
    utc_value = value.astimezone(timezone.utc)
    return utc_value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_evidence_payload(
    raw: object,
    prepared: Mapping[str, object],
    resolved: Mapping[str, object],
    final_queries: tuple[str, str, str],
    record_id: str,
) -> dict[str, object]:
    """Replay product evidence into its only accepted normalized payload."""
    root_value = _exact(raw, {"task_record_id", "platform", "queries", "details"}, "evidence")
    task = Task.from_dict(prepared["task"])
    if root_value["task_record_id"] != record_id:
        raise _error("evidence task record does not match")
    if root_value["platform"] != task.platform.value:
        raise _error("evidence platform does not match")
    query_values = root_value["queries"]
    expected_queries = list(final_queries)
    if not isinstance(query_values, list) or len(query_values) != 3:
        raise _error("evidence must contain exactly three query blocks")
    seen_queries: set[str] = set()
    observations: list[Observation] = []
    normalized_blocks: list[dict[str, object]] = []
    try:
        for raw_block in query_values:
            block = _exact(raw_block, {"query", "observations"}, "query block")
            query = block["query"]
            if not isinstance(query, str) or query not in expected_queries or query in seen_queries:
                raise _error("query blocks do not match the manifest")
            seen_queries.add(query)
            cards = block["observations"]
            if not isinstance(cards, list) or not 30 <= len(cards) <= 50:
                raise _error("each query must contain 30-50 observations")
            parsed: list[Observation] = []
            for card in cards:
                value = Observation.from_dict(card)
                if value.query != query:
                    raise _error("observation query does not match its block")
                if route_platform(value.url) is not task.platform:
                    raise _error("observation host does not match the platform")
                if value.is_ad is None:
                    raise _error("observation ad state must be visible and unambiguous")
                if value.product_id is not None:
                    try:
                        encoded_identity = product_identity(task.platform, value.url)
                        explicit_identity = product_identity(
                            task.platform, value.url, value.product_id
                        )
                    except (TypeError, ValueError):
                        raise _error("observation product identity is invalid") from None
                    url_fallback = f"{task.platform.value}:https://"
                    if (
                        not encoded_identity.startswith(url_fallback)
                        and explicit_identity != encoded_identity
                    ):
                        raise _error("observation product identity conflicts with URL")
                parsed.append(value)
            ranks = sorted(value.rank for value in parsed)
            if ranks != list(range(1, len(parsed) + 1)):
                raise _error("observation ranks must be unique and contiguous")
            observations.extend(parsed)
            normalized_blocks.append({
                "query": query,
                "observations": [_observation_dict(value, task.platform) for value in sorted(parsed, key=lambda item: item.rank)],
            })
    except WorkflowError:
        raise
    except (TypeError, ValueError):
        raise _error("observation evidence is invalid") from None
    if seen_queries != set(expected_queries):
        raise _error("query blocks do not match the manifest")
    try:
        merged = merge_observations(task.platform, observations)
        recurring = eligible_identities(merged, minimum_query_hits=2)
    except (TypeError, ValueError):
        raise _error("observation identities are invalid") from None
    if len(recurring) < 2:
        raise _error("fewer than two recurring identities")
    recurring_pool = _recurring_pool(merged, recurring)
    details = root_value["details"]
    if not isinstance(details, list):
        raise _error("details must be a list")
    if len(details) > len(recurring_pool):
        raise _error("detail outcomes exceed the recurring pool")
    supplied_identities = [
        item.get("identity") if isinstance(item, dict) else None for item in details
    ]
    if supplied_identities != recurring_pool[:len(details)]:
        raise _error("detail outcomes must be an exact recurring-pool prefix in order")
    candidates: list[VerifiedCandidate] = []
    normalized_details: list[dict[str, object]] = []
    detail_ids: set[str] = set()
    profile = VisualProfile.from_dict(prepared["visual_profile"], task.platform)
    qualifying_count = 0
    for index, raw_detail in enumerate(details):
        candidate, normalized = _detail_candidate(
            raw_detail, merged, task.platform, task, profile
        )
        outcome_identity = normalized["identity"]
        if outcome_identity in detail_ids:
            raise _error("detail identities must be unique")
        detail_ids.add(outcome_identity)
        if candidate is not None:
            qualifying_count += 1
            candidates.append(candidate)
            if qualifying_count == task.result_limit and index != len(details) - 1:
                raise _error("detail outcomes continue after the result limit was reached")
        normalized_details.append(normalized)
    if qualifying_count < task.result_limit and len(details) != len(recurring_pool):
        raise _error("detail outcomes stopped before the recurring pool was exhausted")
    normalized_blocks.sort(key=lambda block: expected_queries.index(block["query"]))
    candidate_values = sorted((_candidate_dict(value) for value in candidates), key=lambda value: value["identity"])
    provenance_key, provenance = _resolution_provenance(resolved, final_queries)
    payload = {
        provenance_key: provenance,
        "evidence": {
            "task_record_id": record_id,
            "platform": task.platform.value,
            "queries": normalized_blocks,
            "details": normalized_details,
        },
        "candidates": candidate_values,
        "observation_count": len(observations),
        "recurring_count": len(recurring),
        "detail_count": len(details),
    }
    payload["evidence_digest"] = _canonical_evidence_digest(payload)
    return payload


def _validate_evidence_locked(run_dir, input_path, clock=None):
    clock = _DEFAULT_CLOCK if clock is None else clock
    if not callable(clock):
        raise _error("validation clock is invalid")
    root, record_id, absolute_run = _run_parts(Path(run_dir))
    store = _store(root)
    try:
        checkpoint = store.load(record_id)
        if checkpoint is None:
            raise ValueError
        prepared = _prepared_payload(checkpoint, record_id, absolute_run)
        if checkpoint["stage"] == "finalized":
            raise _error("finalized evidence cannot be replaced")
        if checkpoint.get("version") != 2 or checkpoint["stage"] == "prepared":
            raise WorkflowError(NEXT_STEP)
        resolved, final_queries = _queries_resolved_payload(
            checkpoint, record_id, absolute_run,
        )
        if checkpoint["stage"] == "evidence_validated":
            checkpoint = _migrate_legacy_evidence_digest_locked(
                store, checkpoint, absolute_run,
            )
            prepared = _prepared_payload(checkpoint, record_id, absolute_run)
            resolved, final_queries = _queries_resolved_payload(
                checkpoint, record_id, absolute_run,
            )
            # Validate stored provenance and every derived field before an
            # otherwise harmless refresh can replace corrupted evidence.
            _validated_payload(checkpoint, absolute_run)
        payload = _canonical_evidence_payload(
            _load_json(Path(input_path)), prepared, resolved, final_queries,
            record_id,
        )
        payload["validated_at"] = _validation_timestamp(clock)
        if checkpoint["stage"] == "evidence_validated":
            existing = checkpoint["stages"]["evidence_validated"]
            if not isinstance(existing, dict):
                raise ValueError
            existing_comparable = {
                key: value for key, value in existing.items()
                if key not in {"validated_at", "write_progress"}
            }
            new_comparable = {
                key: value for key, value in payload.items()
                if key != "validated_at"
            }
            if existing_comparable != new_comparable:
                if "write_progress" in existing:
                    raise _error("evidence cannot be replaced after live finalization begins")
                raise _error("evidence cannot be replaced")
            if "write_progress" in existing:
                payload["write_progress"] = existing["write_progress"]
        store.save_stage(record_id, "evidence_validated", payload)
    except WorkflowError:
        raise
    except Exception:
        raise _error("validated evidence checkpoint was rejected") from None
    return (
        f"Validated {payload['observation_count']} observations; "
        f"{payload['recurring_count']} recurring; {payload['detail_count']} details in {absolute_run}"
    )


def _validated_payload(
    checkpoint: Mapping[str, object], run_dir: Path
) -> tuple[Task, Path, list[VerifiedCandidate], dict[str, object]]:
    try:
        stages = checkpoint["stages"]
        prepared = _prepared_payload(checkpoint, checkpoint["record_id"], run_dir)
        evidence = stages["evidence_validated"]
        if not isinstance(evidence, dict):
            raise ValueError
        resolved, final_queries = _queries_resolved_payload(
            checkpoint, checkpoint["record_id"], run_dir,
        )
        provenance_key, _expected_provenance = _resolution_provenance(resolved, final_queries)
        allowed = {
            provenance_key, "evidence", "candidates",
            "observation_count", "recurring_count", "detail_count",
            "evidence_digest", "validated_at", "write_progress",
        }
        legacy_digest_missing = "evidence_digest" not in evidence
        required = allowed - {"write_progress", "evidence_digest"}
        if not set(evidence).issubset(allowed) or not required <= set(evidence):
            raise ValueError
        for count_key in ("observation_count", "recurring_count", "detail_count"):
            count_value = evidence[count_key]
            if type(count_value) is not int or count_value < 0:
                raise ValueError
        _validated_resolution_provenance(
            evidence, checkpoint, checkpoint["record_id"], run_dir,
        )
        canonical = _canonical_evidence_payload(
            evidence["evidence"], prepared, resolved, final_queries,
            checkpoint["record_id"],
        )
        canonical_keys = {
            provenance_key, "evidence", "candidates",
            "observation_count", "recurring_count", "detail_count",
            "evidence_digest",
        }
        compared_keys = canonical_keys - ({"evidence_digest"} if legacy_digest_missing else set())
        if {key: evidence[key] for key in compared_keys} != {
            key: canonical[key] for key in compared_keys
        }:
            raise ValueError
        task = Task.from_dict(prepared["task"])
        source = Path(prepared["source_image"]["path"])
        candidates_raw = canonical["candidates"]
        if not isinstance(candidates_raw, list):
            raise ValueError
        candidates = [VerifiedCandidate.from_dict(item) for item in candidates_raw]
        return task, source, candidates, evidence
    except (KeyError, TypeError, ValueError, WorkflowError):
        raise _error("validated checkpoint is invalid") from None


def _migrate_legacy_evidence_digest_locked(
    store: CheckpointStore,
    checkpoint: Mapping[str, object],
    run_dir: Path,
) -> Mapping[str, object]:
    """Add the v2 evidence digest once, after full replay under the record lock."""
    stages = checkpoint.get("stages")
    if not isinstance(stages, dict):
        raise _error("validated checkpoint is invalid")
    evidence = stages.get("evidence_validated")
    if not isinstance(evidence, dict) or "evidence_digest" in evidence:
        return checkpoint
    _task, _source, _candidates, validated = _validated_payload(checkpoint, run_dir)
    resolved, final_queries = _queries_resolved_payload(
        checkpoint, checkpoint["record_id"], run_dir,
    )
    provenance_key, _expected_provenance = _resolution_provenance(resolved, final_queries)
    core_keys = {
        provenance_key, "evidence", "candidates",
        "observation_count", "recurring_count", "detail_count",
    }
    migrated = dict(validated)
    migrated["evidence_digest"] = _canonical_evidence_digest(
        {key: migrated[key] for key in core_keys}
    )
    try:
        store.save_stage(checkpoint["record_id"], "evidence_validated", migrated)
        refreshed = store.load(checkpoint["record_id"])
    except Exception:
        raise _error("validated evidence checkpoint was rejected") from None
    if refreshed is None:
        raise _error("validated evidence checkpoint was rejected")
    return refreshed


def _digestless_v2_finalized_summary(
    checkpoint: Mapping[str, object], run_dir: Path,
) -> str | None:
    """Read a valid pre-digest v2 completion without mutating it."""
    if checkpoint.get("version") != 2 or checkpoint.get("stage") != "finalized":
        return None
    try:
        stages = checkpoint["stages"]
        if not isinstance(stages, Mapping):
            raise ValueError
        evidence = stages["evidence_validated"]
        if not isinstance(evidence, Mapping):
            raise ValueError
        if "evidence_digest" in evidence:
            return None
        task, _source, candidates, validated = _validated_payload(checkpoint, run_dir)
        selected = select_results(task, candidates)
        intended = [
            json.dumps(
                [task.sku, task.platform.value, value.canonical_url],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for value in selected
        ]
        progress = validated.get("write_progress")
        if not isinstance(progress, Mapping) or set(progress) not in (
            {"intended", "completed"},
            {"intended", "completed", "writes_complete"},
        ):
            raise ValueError
        if progress["intended"] != intended or progress["completed"] != intended:
            raise ValueError
        if "writes_complete" in progress and progress["writes_complete"] is not True:
            raise ValueError
        final = _exact(stages["finalized"], {"result_count", "completed"}, "finalized checkpoint")
        result_count = final["result_count"]
        completed = final["completed"]
        if (
            type(result_count) is not int
            or result_count < 0
            or result_count != len(selected)
            or not isinstance(completed, list)
            or len(completed) != result_count
            or completed != intended
        ):
            raise ValueError
        expected_output = {
            "task_record_id": task.record_id,
            "platform": task.platform.value,
            "result_count": len(selected),
            "results": [_candidate_dict(value) for value in selected],
        }
        output = _exact(
            _load_json(run_dir / "final-results.json"),
            {"task_record_id", "platform", "result_count", "results"},
            "final results",
        )
        output_count = output["result_count"]
        output_results = output["results"]
        if (
            type(output_count) is not int
            or output_count < 0
            or output_count != result_count
            or not isinstance(output_results, list)
            or len(output_results) != output_count
            or output != expected_output
        ):
            raise ValueError
    except WorkflowError:
        raise
    except (KeyError, TypeError, ValueError):
        raise _error("validated checkpoint is invalid") from None
    return f"Already finalized {len(selected)} result(s): {run_dir / 'final-results.json'}"


@contextmanager
def _record_lock(run_dir: Path):
    directory_descriptor = -1
    descriptor = -1
    try:
        directory_descriptor = os.open(
            run_dir,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        open_directory = os.fstat(directory_descriptor)
        named_directory = os.stat(run_dir, follow_symlinks=False)
        if (
            not stat.S_ISDIR(open_directory.st_mode)
            or not stat.S_ISDIR(named_directory.st_mode)
            or (open_directory.st_dev, open_directory.st_ino)
            != (named_directory.st_dev, named_directory.st_ino)
        ):
            raise OSError
        descriptor = os.open(
            ".finalize.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise OSError
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _error("record workflow lock is already held") from None
        yield
    except WorkflowError:
        raise
    except OSError:
        raise _error("record workflow lock could not be acquired") from None
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(descriptor)
            except OSError:
                pass
        if directory_descriptor >= 0:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


def validate_evidence(run_dir, input_path, clock=None):
    _root, _record_id, absolute_run = _run_parts(
        Path(run_dir), verify_checkpoint=False
    )
    with _record_lock(absolute_run):
        return _validate_evidence_locked(absolute_run, input_path, clock=clock)


def _evidence_is_fresh(evidence: Mapping[str, object], clock, max_age: float) -> None:
    timestamp = evidence.get("validated_at")
    if not isinstance(timestamp, str):
        raise _error("validated evidence timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        current = clock()
    except Exception:
        raise _error("validated evidence timestamp is invalid") from None
    if (
        parsed.tzinfo is None
        or not isinstance(current, datetime)
        or current.tzinfo is None
    ):
        raise _error("validated evidence timestamp is invalid")
    age = (current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > max_age:
        raise _error("validated evidence is stale")


def _finalize_locked(
    root: Path,
    record_id: str,
    absolute_run: Path,
    *,
    dry_run: bool,
    lark_client,
    clock,
    evidence_max_age_seconds: float,
):
    if type(dry_run) is not bool:
        raise _error("dry-run flag is invalid")
    store = _store(root)
    try:
        checkpoint = store.load(record_id)
    except Exception:
        raise _error("checkpoint could not be loaded") from None
    if checkpoint is None or checkpoint["stage"] == "prepared":
        raise WorkflowError(NEXT_STEP)
    legacy = _legacy_finalized_summary(checkpoint, absolute_run)
    if legacy is not None:
        return legacy
    if checkpoint.get("version") != 2 or checkpoint["stage"] == "queries_resolved":
        raise WorkflowError(NEXT_STEP)
    if not dry_run:
        checkpoint = _migrate_legacy_evidence_digest_locked(
            store, checkpoint, absolute_run,
        )
    task, source_image, candidates, evidence = _validated_payload(checkpoint, absolute_run)
    selected = select_results(task, candidates)
    output = {
        "task_record_id": task.record_id,
        "platform": task.platform.value,
        "result_count": len(selected),
        "results": [_candidate_dict(value) for value in selected],
    }
    _write_json(absolute_run / "final-results.json", output)
    if dry_run:
        return f"Dry run produced {len(selected)} result(s): {absolute_run / 'final-results.json'}"
    if checkpoint["stage"] == "finalized":
        return f"Already finalized {len(selected)} result(s): {absolute_run / 'final-results.json'}"
    lark = lark_client or LarkBaseClient()
    intended = [
        json.dumps(
            [task.sku, task.platform.value, value.canonical_url],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for value in selected
    ]
    progress = evidence.get("write_progress")
    if progress is None:
        completed: list[str] = []
        writes_complete = False
    else:
        try:
            if not isinstance(progress, dict) or set(progress) not in (
                {"intended", "completed"},
                {"intended", "completed", "writes_complete"},
            ):
                raise ValueError
            progress_value = progress
            if progress_value["intended"] != intended or not isinstance(progress_value["completed"], list):
                raise ValueError
            completed = list(progress_value["completed"])
            if any(item not in intended for item in completed) or len(completed) != len(set(completed)):
                raise ValueError
            if "writes_complete" in progress_value:
                writes_complete = progress_value["writes_complete"]
                if type(writes_complete) is not bool:
                    raise ValueError
            else:
                # Migration for checkpoints written before the durable marker.
                writes_complete = completed == intended
            if writes_complete and completed != intended:
                raise ValueError
        except (TypeError, ValueError, WorkflowError):
            raise _error("write progress is invalid") from None
    evidence_with_progress = dict(evidence)
    evidence_with_progress["write_progress"] = {
        "intended": intended,
        "completed": completed,
        "writes_complete": writes_complete,
    }
    try:
        lark.validate_base_contracts()
        current_task, current_status = lark.get_task_state(record_id)
        if current_task != task:
            raise _error("live task is missing nonpending or changed")
        if current_status == "成功":
            if not writes_complete or completed != intended:
                raise _error("successful live task lacks durable completed progress")
            for candidate in selected:
                if not lark.verify_existing_result(task, candidate, source_image):
                    raise _error("successful live task result verification failed")
            store.save_stage(
                record_id,
                "finalized",
                {"result_count": len(selected), "completed": completed},
            )
            return f"Reconciled {len(selected)} result(s): {absolute_run / 'final-results.json'}"
        if current_status != "未开始":
            raise _error("live task is missing nonpending or changed")
        _evidence_is_fresh(evidence, clock, evidence_max_age_seconds)
        store.save_stage(record_id, "evidence_validated", evidence_with_progress)
        for business_key, candidate in zip(intended, selected, strict=True):
            if business_key in completed:
                continue
            lark.write_result(task, candidate, source_image, dry_run=False)
            completed.append(business_key)
            evidence_with_progress["write_progress"] = {
                "intended": intended,
                "completed": list(completed),
                "writes_complete": False,
            }
            store.save_stage(record_id, "evidence_validated", evidence_with_progress)
        writes_complete = True
        evidence_with_progress["write_progress"] = {
            "intended": intended,
            "completed": list(completed),
            "writes_complete": True,
        }
        store.save_stage(record_id, "evidence_validated", evidence_with_progress)
        lark.set_task_status(record_id, "成功", dry_run=False)
        store.save_stage(
            record_id,
            "finalized",
            {"result_count": len(selected), "completed": completed},
        )
    except WorkflowError:
        raise
    except Exception:
        raise _error("live finalization failed; progress is preserved") from None
    return f"Finalized {len(selected)} result(s): {absolute_run / 'final-results.json'}"


def finalize(
    run_dir,
    dry_run=False,
    lark_client=None,
    clock=None,
    evidence_max_age_seconds=1800.0,
):
    if type(dry_run) is not bool:
        raise _error("dry-run flag is invalid")
    if (
        isinstance(evidence_max_age_seconds, bool)
        or not isinstance(evidence_max_age_seconds, (int, float))
        or not math.isfinite(evidence_max_age_seconds)
        or evidence_max_age_seconds <= 0
    ):
        raise _error("evidence freshness limit is invalid")
    clock = _DEFAULT_CLOCK if clock is None else clock
    if not callable(clock):
        raise _error("validation clock is invalid")
    root, record_id, absolute_run = _run_parts(Path(run_dir), verify_checkpoint=False)
    # v1 finalized runs are explicitly read-only.  Detect them via the
    # descriptor-safe store before acquiring `_record_lock`, which otherwise
    # creates a lock file even though this compatibility path must not write.
    try:
        existing = _store(root).load(record_id)
    except Exception:
        raise _error("checkpoint could not be loaded") from None
    if existing is not None and existing.get("version") == 1:
        legacy = _legacy_finalized_summary(existing, absolute_run)
        if legacy is not None:
            return legacy
        raise _error("legacy checkpoint requires a restarted analysis")
    if existing is not None:
        legacy_v2 = _digestless_v2_finalized_summary(existing, absolute_run)
        if legacy_v2 is not None:
            return legacy_v2
    arguments = {
        "dry_run": dry_run,
        "lark_client": lark_client,
        "clock": clock,
        "evidence_max_age_seconds": float(evidence_max_age_seconds),
    }
    if dry_run:
        return _finalize_locked(root, record_id, absolute_run, **arguments)
    with _record_lock(absolute_run):
        return _finalize_locked(root, record_id, absolute_run, **arguments)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and finalize verified marketplace research")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--record-id")
    prepare_parser.add_argument("--work-root", type=Path, default=Path(".work"))
    prepare_parser.add_argument("--dry-run", action="store_true")
    prepare_parser.add_argument("--restart-analysis", action="store_true")
    validate_parser = commands.add_parser("validate-evidence")
    validate_parser.add_argument("--run-dir", required=True, type=Path)
    validate_parser.add_argument("--input", required=True, type=Path)
    resolve_parser = commands.add_parser(
        "resolve-queries",
        help="Legacy version-2 autocomplete checkpoint compatibility only",
        description="Legacy version-2 autocomplete checkpoint compatibility only",
    )
    resolve_parser.add_argument("--run-dir", required=True, type=Path)
    resolve_parser.add_argument("--input", required=True, type=Path)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--run-dir", required=True, type=Path)
    finalize_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "prepare":
            summary = prepare(
                record_id=arguments.record_id,
                work_root=arguments.work_root,
                dry_run=arguments.dry_run,
                restart_analysis=arguments.restart_analysis,
            )
        elif arguments.command == "validate-evidence":
            summary = validate_evidence(arguments.run_dir, arguments.input)
        elif arguments.command == "resolve-queries":
            summary = resolve_queries(arguments.run_dir, arguments.input)
        else:
            summary = finalize(arguments.run_dir, dry_run=arguments.dry_run)
    except WorkflowError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
