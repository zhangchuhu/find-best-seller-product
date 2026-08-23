"""Crash-safe, secret-free task workflow checkpoints."""

from __future__ import annotations

import errno
from datetime import datetime, timezone
import json
import math
import os
import re
import stat
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path


STAGES = ("prepared", "queries_resolved", "evidence_validated", "finalized")
_LEGACY_V1_STAGES = ("prepared", "evidence_validated", "finalized")

_RECORD_ID = re.compile(r"rec[A-Za-z0-9_-]+", re.ASCII)
_ENVELOPE_KEYS = frozenset({"record_id", "stage", "stages", "version"})
_EVIDENCE_SHARED_CORE_KEYS = frozenset({
    "evidence", "candidates",
    "observation_count", "recurring_count", "detail_count", "evidence_digest",
    "screenshot_manifest", "screenshot_count",
})
_FORBIDDEN_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "cookie",
    "session",
    "access_token",
    "refresh_token",
    "base_token",
    "private_key",
)
_SAFE_IDENTIFIER_KEYS = frozenset(
    {"file_token", "image_token", "record_id", "product_id", "table_id", "view_id"}
)
_FORBIDDEN_STRING_PATTERNS = (
    re.compile(r"ARK_API_KEY", re.IGNORECASE),
    re.compile(r"Authorization\s*:", re.IGNORECASE),
    re.compile(r"\bBearer\s+", re.IGNORECASE),
    re.compile(r"\bCookie\s*:", re.IGNORECASE),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:api[\s_.-]*key|apikey|authorization|password|"
        r"secret|session|access[\s_.-]*token|refresh[\s_.-]*token|"
        r"base[\s_.-]*token|private[\s_.-]*key|token|key)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*[A-Za-z0-9_.-]+\s*=\s*[^;,\s]+\s*$", re.IGNORECASE),
    re.compile(
        r"(?:^|;\s*)[A-Za-z0-9_.-]+\s*=\s*[^;\s]+\s*;\s*"
        r"[A-Za-z0-9_.-]+\s*=\s*[^;\s]+",
        re.IGNORECASE,
    ),
    re.compile(r"data:image/[^;,\s]+;base64,", re.IGNORECASE),
)
_UNSUPPORTED_DIRECTORY_FSYNC = frozenset(
    value
    for value in (
        errno.EINVAL,
        errno.EBADF,
        getattr(errno, "EISDIR", None),
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if value is not None
)


class CheckpointError(RuntimeError):
    """Raised for sanitized checkpoint validation and persistence failures."""


MAX_CHECKPOINT_BYTES = 10 * 1024 * 1024


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")


def _path_text(path: tuple[str | int, ...]) -> str:
    result = "$"
    for component in path:
        if isinstance(component, int):
            addition = f"[{component}]"
        else:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", component)[:32] or "<key>"
            addition = f".{safe}"
        if len(result) + len(addition) > 120:
            return result + ".<truncated>"
        result += addition
    return result


def _rejected(path: tuple[str | int, ...], category: str) -> CheckpointError:
    return CheckpointError(f"checkpoint rejected at {_path_text(path)}: {category}")


def _forbidden_key(key: str) -> bool:
    normalized = _normalized_key(key)
    if key in _SAFE_IDENTIFIER_KEYS:
        return False
    if normalized in _SAFE_IDENTIFIER_KEYS:
        return True
    return any(fragment in normalized for fragment in _FORBIDDEN_KEYS)


def _string_category(value: str, forbidden_values: tuple[str, ...]) -> str | None:
    if any(forbidden in value for forbidden in forbidden_values):
        return "forbidden configured value"
    if any(pattern.search(value) for pattern in _FORBIDDEN_STRING_PATTERNS):
        return "forbidden string"
    return None


def _normalize_graph(
    value: object,
    forbidden_values: tuple[str, ...],
    *,
    path: tuple[str | int, ...] = (),
    active: set[int] | None = None,
) -> object:
    if active is None:
        active = set()
    if value is None or type(value) is bool or type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _rejected(path, "non-finite number")
        return value
    if isinstance(value, str):
        category = _string_category(value, forbidden_values)
        if category is not None:
            raise _rejected(path, category)
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise _rejected(path, "cyclic value")
        active.add(identity)
        try:
            try:
                items = list(value.items())
            except Exception:
                raise _rejected(path, "invalid mapping") from None
            normalized: dict[str, object] = {}
            for key, item in items:
                if not isinstance(key, str):
                    raise _rejected(path + ("<key>",), "non-string key")
                key_category = _string_category(key, forbidden_values)
                if key_category == "forbidden configured value":
                    raise _rejected(path + ("<key>",), key_category)
                if _forbidden_key(key):
                    raise _rejected(path + ("<key>",), "forbidden key")
                if key_category is not None:
                    raise _rejected(path + ("<key>",), key_category)
                normalized[key] = _normalize_graph(
                    item,
                    forbidden_values,
                    path=path + (key,),
                    active=active,
                )
            return normalized
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise _rejected(path, "cyclic value")
        active.add(identity)
        try:
            try:
                return [
                    _normalize_graph(
                        item,
                        forbidden_values,
                        path=path + (index,),
                        active=active,
                    )
                    for index, item in enumerate(value)
                ]
            except CheckpointError:
                raise
            except Exception:
                raise _rejected(path, "invalid sequence") from None
        finally:
            active.remove(identity)
    raise _rejected(path, "unsupported value")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-standard number")


def _evidence_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _evidence_core_keys(value: Mapping[str, object]) -> frozenset[str] | None:
    """Recognize one immutable evidence provenance layout at a time."""
    keys = set(value)
    screenshot_keys = {"screenshot_manifest", "screenshot_count"}
    if not screenshot_keys <= keys:
        return None
    if "autocomplete_provenance" in keys and "query_provenance" not in keys:
        return _EVIDENCE_SHARED_CORE_KEYS | frozenset({"autocomplete_provenance"})
    if "query_provenance" in keys and "autocomplete_provenance" not in keys:
        return _EVIDENCE_SHARED_CORE_KEYS | frozenset({"query_provenance"})
    return None


def _progress_value(value: object) -> tuple[tuple[str, ...], tuple[str, ...], bool] | None:
    if not isinstance(value, dict) or set(value) not in (
        {"intended", "completed"},
        {"intended", "completed", "writes_complete"},
    ):
        return None
    intended = value["intended"]
    completed = value["completed"]
    if (
        not isinstance(intended, list)
        or not isinstance(completed, list)
        or any(not isinstance(item, str) or not item for item in intended)
        or any(not isinstance(item, str) or not item for item in completed)
        or len(intended) != len(set(intended))
        or len(completed) != len(set(completed))
        or completed != intended[:len(completed)]
    ):
        return None
    complete = value.get("writes_complete", completed == intended)
    if type(complete) is not bool or (complete and completed != intended):
        return None
    return tuple(intended), tuple(completed), complete


def _selected_business_keys(
    stages: Mapping[str, object], evidence: Mapping[str, object],
) -> tuple[str, ...] | None:
    """Derive the only legitimate first-write keys from immutable evidence."""
    try:
        from scripts.candidates import select_results
        from scripts.models import Task, VerifiedCandidate

        prepared = stages["prepared"]
        if not isinstance(prepared, Mapping):
            return None
        task = Task.from_dict(prepared["task"])
        candidates_raw = evidence["candidates"]
        if not isinstance(candidates_raw, list):
            return None
        selected = select_results(
            task, [VerifiedCandidate.from_dict(value) for value in candidates_raw],
        )
        return tuple(
            json.dumps(
                [task.sku, task.platform.value, value.canonical_url],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for value in selected
        )
    except (KeyError, TypeError, ValueError):
        return None


def _evidence_replacement_allowed(
    current: object,
    replacement: object,
    stages: Mapping[str, object],
) -> bool:
    """Allow only metadata/progress monotonicity after evidence is durable."""
    if not isinstance(current, dict) or not isinstance(replacement, dict):
        return False
    core = _evidence_core_keys(current)
    if core is None or core != _evidence_core_keys(replacement):
        return False
    legacy_core = core - {"evidence_digest"}
    allowed = core | frozenset({"validated_at", "write_progress"})
    if (
        not ((legacy_core | {"validated_at"}) <= set(current))
        or not ((legacy_core | {"validated_at"}) <= set(replacement))
        or not set(current) <= allowed
        or not set(replacement) <= allowed
    ):
        return False
    if any(current[key] != replacement[key] for key in legacy_core):
        return False
    current_digest = current.get("evidence_digest")
    replacement_digest = replacement.get("evidence_digest")
    if current_digest is not None:
        if replacement_digest != current_digest:
            return False
    elif not isinstance(replacement_digest, str) or len(replacement_digest) != 64:
        # A legacy v2 payload can be refreshed only through the one-way digest
        # migration; remaining digest-less would leave a replacement avenue.
        return False
    current_time = _evidence_timestamp(current["validated_at"])
    replacement_time = _evidence_timestamp(replacement["validated_at"])
    if current_time is None or replacement_time is None or replacement_time < current_time:
        return False
    old_progress = current.get("write_progress")
    new_progress = replacement.get("write_progress")
    if old_progress is None:
        if new_progress is None:
            return True
        new_value = _progress_value(new_progress)
        expected = _selected_business_keys(stages, current)
        return (
            new_value is not None
            and expected is not None
            and new_value == (expected, (), False)
        )
    old_value = _progress_value(old_progress)
    new_value = _progress_value(new_progress)
    if old_value is None or new_value is None:
        return False
    old_intended, old_completed, old_complete = old_value
    new_intended, new_completed, new_complete = new_value
    return (
        old_intended == new_intended
        and new_completed[:len(old_completed)] == old_completed
        and (not old_complete or new_complete)
    )


class CheckpointStore:
    def __init__(
        self,
        root: Path,
        forbidden_values: Iterable[str] = (),
    ) -> None:
        self._root = Path(root)
        self._resolved_root = self._root.resolve(strict=False)
        if isinstance(forbidden_values, str):
            values = (forbidden_values,)
        elif isinstance(forbidden_values, bytes):
            raise TypeError("forbidden_values must be an iterable of strings")
        else:
            try:
                values = tuple(forbidden_values)
            except TypeError:
                raise TypeError("forbidden_values must be an iterable of strings") from None
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError("forbidden_values must contain non-empty strings")
        self._forbidden_values = values

    @staticmethod
    def _validate_record_id(record_id: str) -> None:
        if not isinstance(record_id, str) or _RECORD_ID.fullmatch(record_id) is None:
            raise CheckpointError("invalid record identifier")

    @staticmethod
    def _validate_stage(stage: str) -> None:
        if not isinstance(stage, str) or stage not in STAGES:
            raise CheckpointError("invalid checkpoint stage")

    def _open_root(self, *, create: bool) -> int | None:
        root_descriptor = -1
        try:
            if not self._resolved_root.exists():
                if not create:
                    return None
                self._resolved_root.mkdir(parents=True, exist_ok=True)
            if not self._resolved_root.is_dir():
                raise CheckpointError("checkpoint root is not a directory")
            if self._root.resolve(strict=True) != self._resolved_root:
                raise CheckpointError("checkpoint root changed")
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            root_descriptor = os.open(self._resolved_root, flags)
            if not stat.S_ISDIR(os.fstat(root_descriptor).st_mode):
                raise CheckpointError("checkpoint root is not a directory")
            return root_descriptor
        except CheckpointError:
            self._close_descriptors(root_descriptor)
            raise
        except OSError:
            self._close_descriptors(root_descriptor)
            raise CheckpointError("checkpoint root is unavailable") from None

    @staticmethod
    def _close_descriptors(*descriptors: int) -> None:
        for descriptor in descriptors:
            if descriptor < 0:
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass

    @classmethod
    def _close_task(cls, handle: tuple[int, int, Path]) -> None:
        root_descriptor, task_descriptor, _task = handle
        cls._close_descriptors(task_descriptor, root_descriptor)

    def _verify_task_binding(
        self,
        root_descriptor: int,
        task_descriptor: int,
        record_id: str,
    ) -> None:
        try:
            open_root = os.fstat(root_descriptor)
            named_root = os.stat(self._root)
            open_task = os.fstat(task_descriptor)
            named_task = os.stat(
                record_id,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            raise CheckpointError("checkpoint task directory changed") from None
        if (
            not stat.S_ISDIR(open_root.st_mode)
            or not stat.S_ISDIR(named_root.st_mode)
            or (open_root.st_dev, open_root.st_ino) != (named_root.st_dev, named_root.st_ino)
            or not stat.S_ISDIR(open_task.st_mode)
            or not stat.S_ISDIR(named_task.st_mode)
            or (open_task.st_dev, open_task.st_ino) != (named_task.st_dev, named_task.st_ino)
        ):
            raise CheckpointError("checkpoint task directory changed")

    def _open_task_directory(
        self,
        record_id: str,
        *,
        create: bool,
    ) -> tuple[int, int, Path] | None:
        self._validate_record_id(record_id)
        root_descriptor = self._open_root(create=create)
        if root_descriptor is None:
            return None
        task_descriptor = -1
        try:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            created = False
            try:
                task_descriptor = os.open(record_id, flags, dir_fd=root_descriptor)
            except FileNotFoundError:
                if not create:
                    self._close_descriptors(root_descriptor)
                    return None
                try:
                    os.mkdir(record_id, 0o700, dir_fd=root_descriptor)
                    created = True
                except FileExistsError:
                    pass
                task_descriptor = os.open(record_id, flags, dir_fd=root_descriptor)
            if created and os.name == "posix":
                os.fchmod(task_descriptor, 0o700)
            self._verify_task_binding(root_descriptor, task_descriptor, record_id)
            return root_descriptor, task_descriptor, self._root / record_id
        except CheckpointError:
            self._close_descriptors(task_descriptor, root_descriptor)
            raise
        except OSError:
            self._close_descriptors(task_descriptor, root_descriptor)
            raise CheckpointError("checkpoint task directory is unavailable") from None

    @staticmethod
    def _checkpoint_status(task_descriptor: int) -> os.stat_result | None:
        try:
            status = os.stat(
                "checkpoint.json",
                dir_fd=task_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise CheckpointError("checkpoint could not be accessed") from None
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
            raise CheckpointError("checkpoint file is invalid")
        return status

    def _validated_envelope(
        self,
        loaded: object,
        record_id: str,
    ) -> dict[str, object]:
        if not isinstance(loaded, dict) or frozenset(loaded) != _ENVELOPE_KEYS:
            raise CheckpointError("checkpoint envelope is invalid")
        if type(loaded.get("version")) is not int or loaded["version"] not in (1, 2):
            raise CheckpointError("checkpoint envelope is invalid")
        if loaded.get("record_id") != record_id:
            raise CheckpointError("checkpoint envelope is invalid")
        stages = loaded.get("stages")
        if not isinstance(stages, dict):
            raise CheckpointError("checkpoint envelope is invalid")
        version = loaded["version"]
        stage = loaded.get("stage")
        if version == 1:
            # Legacy Ark-generated queries have no autocomplete provenance.  They
            # remain inspectable only after the old workflow had fully finalized.
            if stage != "finalized" or frozenset(stages) != frozenset(_LEGACY_V1_STAGES):
                raise CheckpointError("checkpoint envelope is invalid")
            expected_stages = frozenset(_LEGACY_V1_STAGES)
        else:
            if not isinstance(stage, str) or stage not in STAGES:
                raise CheckpointError("checkpoint envelope is invalid")
            expected_stages = frozenset(STAGES[: STAGES.index(stage) + 1])
        if frozenset(stages) != expected_stages:
            raise CheckpointError("checkpoint envelope is invalid")
        if any(not isinstance(stages[name], dict) for name in expected_stages):
            raise CheckpointError("checkpoint envelope is invalid")
        normalized = _normalize_graph(loaded, self._forbidden_values)
        if not isinstance(normalized, dict):
            raise CheckpointError("checkpoint envelope is invalid")
        return normalized

    def _load_from_handle(
        self,
        handle: tuple[int, int, Path],
        record_id: str,
    ) -> dict[str, object] | None:
        root_descriptor, task_descriptor, _task = handle
        try:
            self._verify_task_binding(root_descriptor, task_descriptor, record_id)
            if self._checkpoint_status(task_descriptor) is None:
                return None
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            file_descriptor = os.open(
                "checkpoint.json",
                flags,
                dir_fd=task_descriptor,
            )
            try:
                file_status = os.fstat(file_descriptor)
                if not stat.S_ISREG(file_status.st_mode):
                    raise OSError("not a regular file")
                if file_status.st_size > MAX_CHECKPOINT_BYTES:
                    raise CheckpointError("checkpoint exceeds the size limit")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(
                        file_descriptor,
                        min(1024 * 1024, MAX_CHECKPOINT_BYTES + 1 - total),
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_CHECKPOINT_BYTES:
                        raise CheckpointError("checkpoint exceeds the size limit")
                encoded = b"".join(chunks)
            finally:
                if file_descriptor >= 0:
                    self._close_descriptors(file_descriptor)
            self._verify_task_binding(root_descriptor, task_descriptor, record_id)
            loaded = json.loads(
                encoded,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
            )
        except CheckpointError:
            raise
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            raise CheckpointError("checkpoint could not be loaded") from None
        return self._validated_envelope(loaded, record_id)

    def load(self, record_id: str) -> dict[str, object] | None:
        handle = self._open_task_directory(record_id, create=False)
        if handle is None:
            return None
        try:
            return self._load_from_handle(handle, record_id)
        finally:
            self._close_task(handle)

    @staticmethod
    def _fsync_directory(task_descriptor: int) -> None:
        try:
            os.fsync(task_descriptor)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED_DIRECTORY_FSYNC:
                raise

    def _atomic_write(
        self,
        root_descriptor: int,
        task_descriptor: int,
        record_id: str,
        encoded: bytes,
    ) -> None:
        temporary: str | None = None
        try:
            while temporary is None:
                candidate = f".checkpoint.{uuid.uuid4().hex}.tmp"
                flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                try:
                    file_descriptor = os.open(
                        candidate,
                        flags,
                        0o600,
                        dir_fd=task_descriptor,
                    )
                except FileExistsError:
                    continue
                temporary = candidate
            try:
                if os.name == "posix":
                    os.fchmod(file_descriptor, 0o600)
                with os.fdopen(file_descriptor, "wb", buffering=0) as temporary_file:
                    file_descriptor = -1
                    view = memoryview(encoded)
                    offset = 0
                    while offset < len(view):
                        written = os.write(temporary_file.fileno(), view[offset:])
                        if written <= 0:
                            raise OSError("short checkpoint write")
                        offset += written
                    temporary_file.flush()
                    os.fsync(temporary_file.fileno())
            finally:
                if file_descriptor >= 0:
                    os.close(file_descriptor)
            self._verify_task_binding(root_descriptor, task_descriptor, record_id)
            os.replace(
                temporary,
                "checkpoint.json",
                src_dir_fd=task_descriptor,
                dst_dir_fd=task_descriptor,
            )
            temporary = None
            self._verify_task_binding(root_descriptor, task_descriptor, record_id)
            self._fsync_directory(task_descriptor)
        except CheckpointError:
            raise
        except OSError:
            raise CheckpointError("checkpoint could not be saved") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=task_descriptor)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    def save_stage(
        self,
        record_id: str,
        stage: str,
        payload: Mapping[str, object],
    ) -> Path:
        self._validate_record_id(record_id)
        self._validate_stage(stage)
        if not isinstance(payload, Mapping):
            raise _rejected((), "payload is not a mapping")
        normalized = _normalize_graph(payload, self._forbidden_values)
        if not isinstance(normalized, dict):
            raise _rejected((), "payload is not a mapping")

        handle = self._open_task_directory(record_id, create=False)
        if handle is None:
            if stage != STAGES[0]:
                raise CheckpointError("invalid checkpoint transition")
            handle = self._open_task_directory(record_id, create=True)
            assert handle is not None
        root_descriptor, task_descriptor, task = handle
        path = task / "checkpoint.json"
        try:
            current = self._load_from_handle(handle, record_id)
            if current is None:
                if stage != STAGES[0]:
                    raise CheckpointError("invalid checkpoint transition")
                stages: dict[str, object] = {}
            else:
                if current["version"] == 1:
                    raise CheckpointError("legacy checkpoint is read-only")
                current_stage = current["stage"]
                current_index = STAGES.index(current_stage)  # type: ignore[arg-type]
                requested_index = STAGES.index(stage)
                current_stages = current["stages"]
                current_evidence = current_stages.get("evidence_validated")
                if (
                    current_stage == "evidence_validated"
                    and isinstance(current_stages, dict)
                    and (
                        not isinstance(current_evidence, Mapping)
                        or _evidence_core_keys(current_evidence) is None
                    )
                ):
                    raise CheckpointError("historical checkpoint is read-only")
                if requested_index not in (current_index, current_index + 1):
                    raise CheckpointError("invalid checkpoint transition")
                stages = dict(current_stages)
                if requested_index == current_index:
                    existing = stages[stage]
                    if existing != normalized and not (
                        stage == "evidence_validated"
                        and _evidence_replacement_allowed(existing, normalized, stages)
                    ):
                        if (
                            stage == "evidence_validated"
                            and isinstance(existing, dict)
                            and (
                                "write_progress" in existing
                                or "write_progress" in normalized
                            )
                        ):
                            raise CheckpointError("checkpoint write progress is not monotonic")
                        raise CheckpointError("checkpoint current stage is immutable")
            if (
                stage == "evidence_validated"
                and stage not in stages
                and _evidence_core_keys(normalized) is None
            ):
                raise CheckpointError("checkpoint evidence requires screenshot proof")
            if (
                stage == "evidence_validated"
                and stage not in stages
                and "write_progress" in normalized
            ):
                raise CheckpointError("checkpoint write progress must be initialized by workflow")
            stages[stage] = normalized
            envelope: dict[str, object] = {
                "record_id": record_id,
                "stage": stage,
                "stages": stages,
                "version": 2,
            }
            try:
                encoded = json.dumps(
                    envelope,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeEncodeError):
                raise CheckpointError("checkpoint could not be serialized") from None
            self._checkpoint_status(task_descriptor)
            self._atomic_write(
                root_descriptor,
                task_descriptor,
                record_id,
                encoded,
            )
        finally:
            self._close_task(handle)
        return path

    def completed(self, record_id: str, stage: str) -> bool:
        self._validate_stage(stage)
        current = self.load(record_id)
        if current is None:
            return False
        if current["version"] == 1:
            # Version 1 contains no autocomplete provenance.  Its finalized
            # marker must not be interpreted as completion of the new stage.
            return stage in _LEGACY_V1_STAGES
        return STAGES.index(stage) <= STAGES.index(current["stage"])  # type: ignore[arg-type]
