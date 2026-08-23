from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.ark_vision import VisualProfile
from scripts.checkpoint import CheckpointError, CheckpointStore
from scripts.candidates import VerifiedCandidate, select_results
from scripts.models import Task
from scripts.platforms import Platform
import scripts.workflow as workflow_module
from scripts.workflow import (
    NEXT_STEP,
    WorkflowError,
    finalize,
    main,
    prepare,
    resolve_queries,
    validate_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def task(platform: Platform = Platform.SHEIN_US, limit: int = 2) -> Task:
    return Task(
        record_id="rec_source",
        sku="SKU-7",
        image_token="fileToken7",
        image_name="source.jpg",
        platform=platform,
        min_sold=500,
        min_reviews=100,
        min_rating=4.3,
        result_limit=limit,
    )


def profile(platform: Platform = Platform.SHEIN_US) -> VisualProfile:
    query_seeds = (
        ("mini dress", "puff sleeve mini dress", "cocktail dress")
        if platform is Platform.SHEIN_US
        else ("vestido", "vestido manga abullonada", "vestido cóctel")
    )
    return VisualProfile(
        category="dress" if platform is Platform.SHEIN_US else "vestido",
        subtype="mini dress" if platform is Platform.SHEIN_US else "vestido corto",
        silhouette="fitted" if platform is Platform.SHEIN_US else "ajustado",
        fit="slim" if platform is Platform.SHEIN_US else "entallado",
        color="red",
        style=("romantic", "elegant") if platform is Platform.SHEIN_US else ("romántico", "elegante"),
        selling_points=("puff sleeves", "bow detail") if platform is Platform.SHEIN_US else ("manga abullonada", "detalle de moño"),
        construction=("puff sleeves",) if platform is Platform.SHEIN_US else ("manga abullonada",),
        defining_features=("bow", "square neck") if platform is Platform.SHEIN_US else ("moño", "escote cuadrado"),
        exclusions=("maxi",) if platform is Platform.SHEIN_US else ("vestido largo",),
        use_scene="cocktail" if platform is Platform.SHEIN_US else "cóctel",
        query_language="en-US" if platform is Platform.SHEIN_US else "es-MX",
        query_seeds=query_seeds,
    )


class FakeArk:
    def __init__(self, value: VisualProfile | None = None):
        self.value = value or profile()
        self.calls: list[tuple[Path, Platform]] = []

    def analyze(self, image_path: Path, platform: Platform) -> VisualProfile:
        self.calls.append((image_path, platform))
        return self.value


class FakeLark:
    def __init__(self, tasks: list[Task] | None = None, failures=(), status="未开始"):
        self.tasks = tasks if tasks is not None else [task()]
        self.failures = list(failures)
        self.status = status
        self.events: list[object] = []
        self.write_attempt = 0
        self.fail_write_at: int | None = None
        self.fail_status = False
        self.status_started: threading.Event | None = None
        self.status_release: threading.Event | None = None
        self.results: set[str] = set()
        self.outside_download: Path | None = None

    def validate_base_contracts(self) -> None:
        self.events.append("validate")

    def list_pending_tasks(self, record_id=None):
        self.events.append(("list", record_id))
        if self.status != "未开始":
            return [], list(self.failures)
        return list(self.tasks), list(self.failures)

    def get_task_state(self, record_id):
        self.events.append(("list", record_id))
        if len(self.tasks) != 1 or self.tasks[0].record_id != record_id:
            raise RuntimeError("task state unavailable")
        return self.tasks[0], self.status

    def download_source_image(self, value: Task, destination_dir: Path) -> Path:
        self.events.append(("download", value.record_id))
        if self.outside_download is not None:
            self.outside_download.write_bytes(b"outside image")
            return self.outside_download
        destination_dir.mkdir(parents=True, exist_ok=True)
        path = destination_dir / value.image_name
        path.write_bytes(b"image")
        return path

    def write_result(self, value, candidate, source_image, dry_run=False):
        self.write_attempt += 1
        self.events.append(("write", candidate.identity, dry_run, source_image.name))
        if self.fail_write_at == self.write_attempt:
            raise RuntimeError("Bearer top-secret")
        self.results.add(candidate.identity)
        return object()

    def verify_existing_result(self, value, candidate, source_image):
        self.events.append(("verify", candidate.identity))
        return candidate.identity in self.results

    def set_task_status(self, record_id, status, dry_run=False):
        self.events.append(("status", record_id, status, dry_run))
        if self.status_started is not None:
            self.status_started.set()
        if self.status_release is not None:
            self.status_release.wait(5)
        if self.fail_status:
            raise RuntimeError("retryable status failure")
        self.status = status


class ConcurrentLark:
    def __init__(self, event_path: Path):
        self.event_path = event_path

    def validate_base_contracts(self):
        return None

    def list_pending_tasks(self, record_id=None):
        return [task(limit=1)], []

    def get_task_state(self, record_id):
        return task(limit=1), "未开始"

    def write_result(self, value, candidate, source_image, dry_run=False):
        descriptor = os.open(self.event_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, b"write\n")
        finally:
            os.close(descriptor)
        time.sleep(0.2)

    def set_task_status(self, record_id, status, dry_run=False):
        return None


def concurrent_finalize(run_dir: str, event_path: str, start):
    start.wait()
    try:
        finalize(
            Path(run_dir),
            lark_client=ConcurrentLark(Path(event_path)),
            clock=frozen_clock(),
        )
    except WorkflowError:
        pass


def frozen_clock(value="2026-08-21T10:00:00+00:00"):
    instant = datetime.fromisoformat(value)
    return lambda: instant


def load_fixture(name: str) -> dict[str, object]:
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    # Product-card fixtures predate autocomplete and low-value-feature
    # exclusion. Keep identities and metrics, but bind query/feature evidence
    # to the current durable contracts.
    replacements = {
        "red fitted bow dress": "mini dress",
        "red dress puff sleeves": "puff sleeve mini dress",
        "red dress bow detail": "cocktail dress",
        "vestido ajustado con moño": "vestido",
        "vestido con mangas abullonadas": "vestido manga abullonada",
        "vestido detalle moño": "vestido cóctel",
    }
    if isinstance(value, dict) and isinstance(value.get("queries"), list):
        for block in value["queries"]:
            if isinstance(block, dict):
                block["query"] = replacements.get(block.get("query"), block.get("query"))
                for observation in block.get("observations", []):
                    if isinstance(observation, dict):
                        observation["query"] = replacements.get(observation.get("query"), observation.get("query"))
    if isinstance(value, dict) and isinstance(value.get("details"), list):
        for detail in value["details"]:
            if isinstance(detail, dict) and isinstance(detail.get("visual_features"), list):
                detail["visual_features"] = [
                    feature for feature in detail["visual_features"] if feature != "red"
                ]
    return value


def evidence_fixture(root: Path, name: str) -> Path:
    path = root / name
    path.write_text(json.dumps(load_fixture(name), ensure_ascii=False), encoding="utf-8")
    return path


def prepare_only(root: Path, platform: Platform = Platform.SHEIN_US, limit: int = 2):
    fake_lark = FakeLark([task(platform, limit)])
    fake_ark = FakeArk(profile(platform))
    return prepare(
        record_id="rec_source",
        work_root=root,
        dry_run=True,
        lark_client=fake_lark,
        ark_client=fake_ark,
    ), fake_lark, fake_ark


def prepare_run(root: Path, platform: Platform = Platform.SHEIN_US, limit: int = 2):
    return prepare_only(root, platform, limit)


class PrepareTests(unittest.TestCase):
    def test_prepare_scopes_calls_and_binds_direct_ark_queries_without_base_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, lark, ark = prepare_only(root)
            manifest_path = root.resolve() / "rec_source" / "manifest.json"
            self.assertIn(str(manifest_path), summary)
            self.assertEqual(["validate", ("list", "rec_source"), ("download", "rec_source")], lark.events)
            self.assertEqual(1, len(ark.calls))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("rec_source", manifest["task"]["record_id"])
            self.assertEqual(3, len(manifest["query_seeds"]))
            self.assertEqual(
                ["mini dress", "puff sleeve mini dress", "cocktail dress"],
                manifest["queries"],
            )
            self.assertEqual(str((root / "rec_source" / "source.jpg").resolve()), manifest["source_image"]["path"])
            checkpoint = CheckpointStore(root.resolve()).load("rec_source")
            self.assertEqual("queries_resolved", checkpoint["stage"])
            self.assertEqual("ark_seeds", checkpoint["stages"]["queries_resolved"]["source"])
            self.assertEqual(manifest["queries"], checkpoint["stages"]["queries_resolved"]["queries"])
            self.assertEqual(0o600, manifest_path.stat().st_mode & 0o777)

    def test_restart_after_direct_resolution_is_rejected_without_new_ark_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, lark, ark = prepare_only(root)
            prepare("rec_source", root, True, lark_client=lark, ark_client=ark)
            self.assertEqual(1, len(ark.calls))
            with self.assertRaisesRegex(WorkflowError, "restart-analysis cannot replace direct queries"):
                prepare("rec_source", root, True, True, lark_client=lark, ark_client=ark)
            self.assertEqual(1, len(ark.calls))
            validate_evidence(root / "rec_source", evidence_fixture(root, "shein-evidence.json"))
            with self.assertRaisesRegex(WorkflowError, "restart-analysis"):
                prepare("rec_source", root, True, True, lark_client=lark, ark_client=ark)

    def test_prepare_uses_the_record_lock_before_writing_direct_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_lock = workflow_module._record_lock
            with patch("scripts.workflow._record_lock", wraps=real_lock) as locked:
                prepare_only(root)
            self.assertEqual(1, locked.call_count)
            self.assertEqual((root / "rec_source").resolve(), locked.call_args.args[0])

    def test_resume_rejects_a_missing_or_relocated_source_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, lark, ark = prepare_only(root)
            (root / "rec_source" / "source.jpg").unlink()
            with self.assertRaisesRegex(WorkflowError, "prepared checkpoint"):
                prepare("rec_source", root, True, lark_client=lark, ark_client=ark)

    def test_no_record_reports_task_failures_and_first_task_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            lark = FakeLark([], failures=[type("Failure", (), {"record_id": "recBad", "message": "bad threshold"})()])
            with self.assertRaisesRegex(WorkflowError, "pending task"):
                prepare(work_root=Path(directory), lark_client=lark, ark_client=FakeArk())
        with tempfile.TemporaryDirectory() as directory:
            later = task()
            first = Task(**{**later.__dict__, "record_id": "rec_a"})
            lark = FakeLark([later, first])
            summary = prepare(work_root=Path(directory), dry_run=True, lark_client=lark, ark_client=FakeArk())
            self.assertIn("rec_a", summary)

    def test_selected_record_mismatch_and_default_ark_errors_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            mismatched = Task(**{**task().__dict__, "record_id": "rec_other"})
            with self.assertRaisesRegex(WorkflowError, "record"):
                prepare("rec_source", Path(directory), True, lark_client=FakeLark([mismatched]), ark_client=FakeArk())
        with tempfile.TemporaryDirectory() as directory:
            with patch("scripts.workflow.ArkVisionClient", side_effect=RuntimeError("Bearer private-value")):
                with self.assertRaises(WorkflowError) as raised:
                    prepare("rec_source", Path(directory), True, lark_client=FakeLark())
            self.assertNotIn("private-value", str(raised.exception))

    def test_downloaded_source_must_remain_beneath_the_run_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lark = FakeLark()
            lark.outside_download = root / "outside.jpg"
            ark = FakeArk()
            with self.assertRaisesRegex(WorkflowError, "source image"):
                prepare("rec_source", root, True, lark_client=lark, ark_client=ark)
            self.assertEqual([], ark.calls)

    def test_live_prepare_marks_only_a_terminal_invalid_selected_row_failed(self):
        terminal = type(
            "Failure",
            (),
            {"record_id": "rec_source", "message": "SKU is invalid", "terminal": True},
        )()
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as directory:
                lark = FakeLark([], failures=[terminal])
                with self.assertRaises(WorkflowError) as raised:
                    prepare(
                        "rec_source", Path(directory), dry_run=dry_run,
                        lark_client=lark, ark_client=FakeArk(),
                    )
                self.assertIn("rec_source", str(raised.exception))
                self.assertIn("SKU is invalid", str(raised.exception))
                statuses = [event for event in lark.events if isinstance(event, tuple) and event[0] == "status"]
                self.assertEqual([] if dry_run else [("status", "rec_source", "失败", False)], statuses)

    def test_missing_or_nonpending_scope_is_never_marked_failed(self):
        missing = type(
            "Failure",
            (),
            {"record_id": "rec_source", "message": "record scope is missing or not pending", "terminal": False},
        )()
        with tempfile.TemporaryDirectory() as directory:
            lark = FakeLark([], failures=[missing])
            with self.assertRaises(WorkflowError):
                prepare("rec_source", Path(directory), dry_run=False, lark_client=lark, ark_client=FakeArk())
            self.assertFalse(any(isinstance(event, tuple) and event[0] == "status" for event in lark.events))


class EvidenceTests(unittest.TestCase):
    def _run(self, fixture="shein-evidence.json", platform=Platform.SHEIN_US):
        context = tempfile.TemporaryDirectory()
        root = Path(context.name)
        prepare_run(root, platform)
        summary = validate_evidence(
            root / "rec_source",
            evidence_fixture(root, fixture),
            clock=frozen_clock(),
        )
        return context, root, summary

    def test_both_fixtures_are_valid_and_exactly_30_by_three(self):
        for fixture, platform in (("shein-evidence.json", Platform.SHEIN_US), ("mercado-evidence.json", Platform.MERCADO_MX)):
            with self.subTest(fixture=fixture):
                context, root, summary = self._run(fixture, platform)
                try:
                    self.assertIn("90 observations", summary)
                    loaded = CheckpointStore(root).load("rec_source")
                    evidence = loaded["stages"]["evidence_validated"]
                    self.assertEqual(2, evidence["recurring_count"])
                    self.assertEqual(2, len(evidence["candidates"]))
                finally:
                    context.cleanup()

    def test_shein_qualified_details_may_omit_sold_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root, Platform.SHEIN_US)
            value = load_fixture("shein-evidence.json")
            for detail in value["details"]:
                detail["sold_display"] = None
            path = root / "shein-without-sold.json"
            path.write_text(json.dumps(value), encoding="utf-8")

            validate_evidence(root / "rec_source", path, clock=frozen_clock())

            candidates = CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]["candidates"]
            self.assertEqual([None, None], [item["sold_value"] for item in candidates])

    def test_mercado_qualified_details_may_omit_reviews_and_rating(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root, Platform.MERCADO_MX)
            value = load_fixture("mercado-evidence.json")
            for detail in value["details"]:
                detail["reviews_display"] = None
                detail["rating_display"] = None
            path = root / "mercado-without-reviews-rating.json"
            path.write_text(json.dumps(value), encoding="utf-8")

            validate_evidence(root / "rec_source", path, clock=frozen_clock())

            candidates = CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]["candidates"]
            self.assertEqual([None, None], [item["reviews_value"] for item in candidates])
            self.assertEqual([None, None], [item["rating_value"] for item in candidates])

    def test_product_evidence_uses_exact_direct_ark_seeds_and_persists_provenance(self):
        """Catch a validator that accepts a forged direct resolution."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_only(root)
            exact_path = evidence_fixture(root, "shein-evidence.json")
            validate_evidence(root / "rec_source", exact_path, clock=frozen_clock())

            checkpoint = CheckpointStore(root).load("rec_source")
            validated = checkpoint["stages"]["evidence_validated"]
            resolved = checkpoint["stages"]["queries_resolved"]
            self.assertEqual(
                {"source": "ark_seeds", "queries": resolved["queries"]},
                validated["query_provenance"],
            )
            self.assertEqual(
                ["mini dress", "puff sleeve mini dress", "cocktail dress"],
                [block["query"] for block in validated["evidence"]["queries"]],
            )

    def test_qualified_visual_features_reject_low_value_terms_without_rewriting(self):
        """Catch any feature filter that strips a color or size claim instead of failing closed."""
        cases = (
            (Platform.SHEIN_US, "red embroidery"),
            (Platform.SHEIN_US, "black bow"),
            (Platform.SHEIN_US, "rojo bordado"),
            (Platform.SHEIN_US, "unitalla"),
            (Platform.MERCADO_MX, "rojo bordado"),
            (Platform.MERCADO_MX, "talla M"),
            (Platform.MERCADO_MX, "red embroidery"),
            (Platform.MERCADO_MX, "one size"),
            (Platform.SHEIN_US, "plus size"),
            (Platform.SHEIN_US, "petite"),
            (Platform.SHEIN_US, "tall"),
            (Platform.SHEIN_US, "re\u200bd embroidery"),
            (Platform.MERCADO_MX, "r\u200bedo bordado"),
            (Platform.SHEIN_US, "pl\u200bus size"),
        )
        for platform, feature in cases:
            with self.subTest(platform=platform, feature=feature), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare_run(root, platform)
                value = load_fixture(
                    "shein-evidence.json" if platform is Platform.SHEIN_US else "mercado-evidence.json"
                )
                value["details"][0]["visual_features"] = [feature]
                path = root / "forbidden-feature.json"
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(WorkflowError, "visual features"):
                    validate_evidence(root / "rec_source", path, clock=frozen_clock())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_color_profile = replace(profile(), color="ultraviolet")
            prepare(
                "rec_source", root, True,
                lark_client=FakeLark(), ark_client=FakeArk(source_color_profile),
            )
            value = load_fixture("shein-evidence.json")
            value["details"][0]["visual_features"] = ["ultraviolet embroidery"]
            path = root / "source-color-feature.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "visual features"):
                validate_evidence(root / "rec_source", path, clock=frozen_clock())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_color_profile = replace(profile(), color="apricot beige")
            prepare(
                "rec_source", root, True,
                lark_client=FakeLark(), ark_client=FakeArk(source_color_profile),
            )
            value = load_fixture("shein-evidence.json")
            value["details"][0]["visual_features"] = ["apricot embroidery"]
            path = root / "multiword-source-color-feature.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "visual features"):
                validate_evidence(root / "rec_source", path, clock=frozen_clock())

    def test_valid_features_and_rejected_feature_nullability_remain_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            value = load_fixture("shein-evidence.json")
            value["details"][0]["visual_features"] = [
                "3D flower applique", "puff sleeves", "A-line skirt",
            ]
            path = root / "valid-features.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            validate_evidence(root / "rec_source", path, clock=frozen_clock())
            stored = CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]
            self.assertEqual(
                ["3D flower applique", "puff sleeves", "A-line skirt"],
                stored["candidates"][0]["visual_features"],
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            value = load_fixture("shein-evidence.json")
            value["details"][0].update({
                "status": "rejected", "reason": "threshold_failure",
                "sold_display": "1 sold", "match_level": None,
                "visual_features": ["red embroidery"],
            })
            path = root / "rejected-claim.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "rejected detail"):
                validate_evidence(root / "rec_source", path, clock=frozen_clock())

    def test_strict_structural_and_marketplace_rejections(self):
        mutations = {
            "unknown root": lambda value: value.update({"extra": 1}),
            "unknown observation": lambda value: value["queries"][0]["observations"][0].update({"extra": 1}),
            "missing detail": lambda value: value["details"][0].pop("rating_display"),
            "unknown query": lambda value: value["queries"][0].update({"query": "unknown"}),
            "too few": lambda value: value["queries"][0]["observations"].pop(),
            "too many": lambda value: value["queries"][0]["observations"].extend(copy.deepcopy(value["queries"][0]["observations"][:21])),
            "duplicate rank": lambda value: value["queries"][0]["observations"][1].update({"rank": 1}),
            "rank sequence": lambda value: value["queries"][0]["observations"][1].update({"rank": 99}),
            "unsupported host": lambda value: value["queries"][0]["observations"][0].update({"url": "https://example.com/item"}),
            "unknown ad state": lambda value: value["queries"][0]["observations"][0].update({"is_ad": None}),
            "wrong platform": lambda value: value.update({"platform": "mercado-libre-mx"}),
            "wrong record": lambda value: value.update({"task_record_id": "rec_other"}),
            "duplicate query": lambda value: value["queries"][1].update({"query": value["queries"][0]["query"]}),
            "identity mismatch": lambda value: value["details"][0].update({"identity": "shein-us:999999"}),
            "duplicate detail": lambda value: value["details"].append(copy.deepcopy(value["details"][0])),
            "ambiguous metric": lambda value: value["details"][0].update({"rating_display": "Excellent"}),
            "secret": lambda value: value["details"][0].update({"visual_features": ["Authorization: Bearer x"]}),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare_run(root)
                value = load_fixture("shein-evidence.json")
                mutate(value)
                path = root / "input.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(WorkflowError):
                    validate_evidence(root / "rec_source", path)

    def test_duplicate_json_keys_and_fewer_than_two_recurring_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            duplicate = root / "duplicate.json"
            duplicate.write_text('{"task_record_id":"rec_source","task_record_id":"rec_source","platform":"shein-us","queries":[],"details":[]}', encoding="utf-8")
            with self.assertRaises(WorkflowError):
                validate_evidence(root / "rec_source", duplicate)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            value = load_fixture("shein-evidence.json")
            for block_index, block in enumerate(value["queries"]):
                for index, observation in enumerate(block["observations"]):
                    observation["product_id"] = str(700000 + block_index * 100 + index)
                    observation["url"] = f"https://us.shein.com/unique-p-{700000 + block_index * 100 + index}.html"
            value["details"] = []
            path = root / "input.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "recurring"):
                validate_evidence(root / "rec_source", path)

    def test_evidence_size_limit_is_checked_before_json_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            oversized = root / "oversized.json"
            oversized.write_bytes(b"{" + b"x" * 32)
            with patch("scripts.workflow.MAX_EVIDENCE_JSON_BYTES", 16):
                with self.assertRaisesRegex(WorkflowError, "size"):
                    validate_evidence(root / "rec_source", oversized)

    def test_evidence_descriptor_reader_caps_growth_and_handles_short_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            evidence = root / "evidence.json"
            evidence.write_bytes(b"{}")
            chunks = iter((b"{}", b"x" * 9, b""))
            with patch("scripts.workflow.MAX_EVIDENCE_JSON_BYTES", 8), patch(
                "scripts.workflow.os.read", side_effect=lambda _fd, _size: next(chunks)
            ):
                with self.assertRaisesRegex(WorkflowError, "size"):
                    workflow_module._load_json(evidence)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            evidence = root / "evidence.json"
            evidence.write_bytes((FIXTURES / "shein-evidence.json").read_bytes())
            real_read = os.read
            with patch(
                "scripts.workflow.os.read",
                side_effect=lambda descriptor, size: real_read(descriptor, min(size, 17)),
            ):
                loaded = workflow_module._load_json(evidence)
            self.assertEqual("rec_source", loaded["task_record_id"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO support is required")
    def test_evidence_fifo_is_rejected_without_blocking_on_open(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "evidence.json"
            os.mkfifo(fifo, 0o600)
            program = (
                "import sys; from pathlib import Path; "
                "from scripts.workflow import WorkflowError, _load_json; "
                "\ntry: _load_json(Path(sys.argv[1]))"
                "\nexcept WorkflowError: raise SystemExit(0)"
                "\nraise SystemExit(2)"
            )
            completed = subprocess.run(
                [sys.executable, "-c", program, str(fifo)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_explicit_product_id_must_match_the_id_encoded_in_url_on_both_platforms(self):
        cases = (
            ("shein-evidence.json", Platform.SHEIN_US, "999999"),
            ("mercado-evidence.json", Platform.MERCADO_MX, "MLM999999"),
        )
        for fixture, platform, false_id in cases:
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare_run(root, platform)
                value = load_fixture(fixture)
                # Keep the recurring URL but forge its explicit identity in two query blocks.
                value["queries"][0]["observations"][1]["product_id"] = false_id
                value["queries"][1]["observations"][0]["product_id"] = false_id
                path = root / "forged-id.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(WorkflowError, "identit"):
                    validate_evidence(root / "rec_source", path)

    def test_details_must_be_an_exact_ordered_prefix_and_cannot_stop_early(self):
        mutations = {
            "empty": lambda value: value.update({"details": []}),
            "partial rejection": lambda value: value.update({"details": [
                {**value["details"][0], "status": "rejected", "reason": "threshold_failure",
                 "reviews_display": "1", "match_level": None, "visual_features": None}
            ]}),
            "out of order": lambda value: value["details"].reverse(),
            "skipped": lambda value: value.update({"details": [value["details"][1]]}),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare_run(root)
                value = load_fixture("shein-evidence.json")
                mutate(value)
                path = root / "evidence.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(WorkflowError, "detail|exhaust|prefix|order"):
                    validate_evidence(root / "rec_source", path, clock=frozen_clock())

    def test_qualified_details_are_identity_bound_and_must_meet_platform_thresholds(self):
        mutations = {
            "below threshold": lambda detail: detail.update({"reviews_display": "99 reviews"}),
            "unrelated url": lambda detail: detail.update({"detail_url": "https://us.shein.com/other-p-999999.html", "product_id": "999999"}),
            "url id conflict": lambda detail: detail.update({"product_id": "999999"}),
            "search title reused": lambda detail: detail.update({"title": ""}),
            "unrelated category": lambda detail: detail.update({"category": "shoes"}),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare_run(root)
                value = load_fixture("shein-evidence.json")
                mutate(value["details"][0])
                path = root / "evidence.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(WorkflowError):
                    validate_evidence(root / "rec_source", path, clock=frozen_clock())

    def test_exhaustive_rejected_outcomes_allow_a_complete_zero_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            value = load_fixture("shein-evidence.json")
            for detail in value["details"]:
                detail.update({
                    "status": "rejected",
                    "reason": "threshold_failure",
                    "reviews_display": "1",
                    "match_level": None,
                    "visual_features": None,
                })
            path = root / "zero.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            summary = validate_evidence(
                root / "rec_source", path, clock=frozen_clock()
            )
            self.assertIn("2 details", summary)
            evidence = CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]
            self.assertEqual([], evidence["candidates"])
            self.assertEqual("2026-08-21T10:00:00Z", evidence["validated_at"])

    def test_rejection_reasons_encode_changed_identity_and_inaccessible_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            value = load_fixture("shein-evidence.json")
            value["details"][0].update({
                "status": "rejected",
                "reason": "identity_changed",
                "detail_url": "https://us.shein.com/changed-p-999999.html",
                "product_id": "999999",
                "match_level": None,
                "visual_features": None,
            })
            value["details"][1].update({
                "status": "rejected",
                "reason": "detail_inaccessible",
                "detail_url": None,
                "product_id": None,
                "title": None,
                "category": None,
                "sold_display": None,
                "reviews_display": None,
                "rating_display": None,
                "match_level": None,
                "visual_features": None,
            })
            path = root / "rejected.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            validate_evidence(root / "rec_source", path, clock=frozen_clock())
            candidates = CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]["candidates"]
            self.assertEqual([], candidates)

    def test_rejection_reason_fields_must_be_semantically_consistent(self):
        def inaccessible_with_claimed_detail(detail):
            detail.update({
                "status": "rejected", "reason": "detail_inaccessible",
                "match_level": None, "visual_features": None,
            })

        def threshold_without_detail_title(detail):
            detail.update({
                "status": "rejected", "reason": "threshold_failure",
                "title": None, "match_level": None, "visual_features": None,
            })

        for label, mutate in (
            ("inaccessible with claimed fields", inaccessible_with_claimed_detail),
            ("threshold without title", threshold_without_detail_title),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare_run(root)
                value = load_fixture("shein-evidence.json")
                mutate(value["details"][0])
                path = root / "invalid-reason.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(WorkflowError):
                    validate_evidence(root / "rec_source", path, clock=frozen_clock())

    def test_rejection_reasons_must_describe_a_real_threshold_or_category_failure(self):
        def false_threshold(detail):
            detail.update({
                "status": "rejected", "reason": "threshold_failure",
                "match_level": None, "visual_features": None,
            })

        def false_category(detail):
            detail.update({
                "status": "rejected", "reason": "category_mismatch",
                "match_level": None, "visual_features": None,
            })

        def threshold_hiding_category_drift(detail):
            detail.update({
                "status": "rejected", "reason": "threshold_failure",
                "category": "shoes", "sold_display": "1 sold",
                "match_level": None, "visual_features": None,
            })

        for label, mutate in (
            ("passing metrics called threshold failure", false_threshold),
            ("matching category called mismatch", false_category),
            ("threshold reason hides category drift", threshold_hiding_category_drift),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare_run(root)
                value = load_fixture("shein-evidence.json")
                mutate(value["details"][0])
                path = root / "false-reason.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(WorkflowError):
                    validate_evidence(root / "rec_source", path, clock=frozen_clock())


class QueryResolutionTests(unittest.TestCase):
    def test_prepare_binds_exact_ark_seeds_and_evidence_needs_no_autocomplete_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_only(root)
            run_dir = root / "rec_source"
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(["mini dress", "puff sleeve mini dress", "cocktail dress"], manifest["query_seeds"])
            self.assertEqual(manifest["query_seeds"], manifest["queries"])
            checkpoint = CheckpointStore(root).load("rec_source")
            self.assertEqual("queries_resolved", checkpoint["stage"])
            resolved = checkpoint["stages"]["queries_resolved"]
            self.assertEqual(["mini dress", "puff sleeve mini dress", "cocktail dress"], resolved["queries"])
            self.assertEqual("ark_seeds", resolved["source"])
            validate_evidence(run_dir, evidence_fixture(root, "shein-evidence.json"), clock=frozen_clock())
            validated = CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]
            self.assertEqual(
                {"source": "ark_seeds", "queries": manifest["query_seeds"]},
                validated["query_provenance"],
            )

    def test_prepare_checkpoint_survives_manifest_crash_and_identical_retry_repairs_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "rec_source"
            with patch("scripts.workflow._write_json", side_effect=WorkflowError("manifest crash")):
                with self.assertRaisesRegex(WorkflowError, "manifest crash"):
                    prepare_only(root)
            self.assertEqual("queries_resolved", CheckpointStore(root).load("rec_source")["stage"])
            prepare_only(root)
            self.assertIn("queries", json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")))

    def test_prepared_only_checkpoint_retry_persists_direct_resolution_and_repairs_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_save = CheckpointStore.save_stage

            def crash_after_prepared(store, record_id, stage, payload):
                if stage == "queries_resolved":
                    raise RuntimeError("crash after prepared checkpoint")
                return original_save(store, record_id, stage, payload)

            with patch(
                "scripts.workflow.CheckpointStore.save_stage",
                autospec=True,
                side_effect=crash_after_prepared,
            ):
                with self.assertRaisesRegex(WorkflowError, "prepared checkpoint could not be saved"):
                    prepare_only(root)
            self.assertEqual("prepared", CheckpointStore(root).load("rec_source")["stage"])
            self.assertFalse((root / "rec_source" / "manifest.json").exists())

            prepare_only(root)
            checkpoint = CheckpointStore(root).load("rec_source")
            self.assertEqual("queries_resolved", checkpoint["stage"])
            manifest = json.loads((root / "rec_source" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(["mini dress", "puff sleeve mini dress", "cocktail dress"], manifest["queries"])

    def test_direct_resolution_rejects_forged_source_marker_and_reordered_seeds(self):
        for mutation in (
            {"source": "autocomplete"},
            {"queries": ["mini dress", "cocktail dress", "puff sleeve mini dress"]},
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare_only(root)
                checkpoint_path = root / "rec_source" / "checkpoint.json"
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                checkpoint["stages"]["queries_resolved"].update(mutation)
                checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
                with self.assertRaisesRegex(WorkflowError, "resolved queries checkpoint"):
                    validate_evidence(
                        root / "rec_source", evidence_fixture(root, "shein-evidence.json"), clock=frozen_clock(),
                    )

    def test_legacy_autocomplete_resolved_checkpoint_validates_finalizes_and_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_only(root)
            checkpoint_path = root / "rec_source" / "checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["stage"] = "prepared"
            checkpoint["stages"].pop("queries_resolved")
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

            resolve_queries(root / "rec_source", FIXTURES / "shein-autocomplete.json")
            validate_evidence(
                root / "rec_source", evidence_fixture(root, "shein-evidence.json"), clock=frozen_clock(),
            )
            evidence = CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]
            self.assertIn("autocomplete_provenance", evidence)
            lark = FakeLark()
            self.assertIn("Finalized 2", finalize(root / "rec_source", lark_client=lark, clock=frozen_clock()))
            self.assertIn("Already finalized 2", finalize(root / "rec_source", lark_client=lark, clock=frozen_clock()))

    def test_legacy_finalized_returns_without_parsing_obsolete_profile_or_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "rec_source"
            run_dir.mkdir()
            (run_dir / "final-results.json").write_text(
                json.dumps({"task_record_id": "rec_source", "platform": "shein-us", "result_count": 0, "results": []}),
                encoding="utf-8",
            )
            (run_dir / "checkpoint.json").write_text(json.dumps({
                "record_id": "rec_source", "stage": "finalized", "version": 1,
                "stages": {
                    "prepared": {"legacy_ark_profile": "unparseable"},
                    "evidence_validated": {"legacy": True},
                    "finalized": {"result_count": 0, "completed": []},
                },
            }), encoding="utf-8")
            with patch("scripts.workflow._record_lock", side_effect=AssertionError("legacy must not lock")):
                self.assertIn("Already finalized 0", finalize(run_dir, lark_client=FakeLark()))

    def test_legacy_v1_final_results_count_requires_exact_nonnegative_int(self):
        """The older read-only path also rejects bool/float result counts."""
        for output_count in (False, 0.0):
            with self.subTest(output_count=output_count), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run_dir = root / "rec_source"
                run_dir.mkdir()
                checkpoint_path = run_dir / "checkpoint.json"
                results_path = run_dir / "final-results.json"
                checkpoint_path.write_text(json.dumps({
                    "record_id": "rec_source", "stage": "finalized", "version": 1,
                    "stages": {
                        "prepared": {"legacy_ark_profile": "opaque"},
                        "evidence_validated": {"legacy": True},
                        "finalized": {"result_count": 0, "completed": []},
                    },
                }), encoding="utf-8")
                results_path.write_text(json.dumps({
                    "task_record_id": "rec_source", "platform": "shein-us",
                    "result_count": output_count, "results": [],
                }), encoding="utf-8")
                before_checkpoint = checkpoint_path.read_bytes()
                before_results = results_path.read_bytes()
                lark = FakeLark()

                with self.assertRaisesRegex(WorkflowError, "legacy finalized checkpoint"):
                    finalize(run_dir, lark_client=lark)

                self.assertEqual([], lark.events)
                self.assertEqual(before_checkpoint, checkpoint_path.read_bytes())
                self.assertEqual(before_results, results_path.read_bytes())
                self.assertFalse((run_dir / ".finalize.lock").exists())

    def test_v2_finalize_still_acquires_the_record_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_only(root)
            real_lock = workflow_module._record_lock
            with patch("scripts.workflow._record_lock", wraps=real_lock) as locked:
                with self.assertRaises(WorkflowError):
                    finalize(root / "rec_source", lark_client=FakeLark())
            self.assertEqual(1, locked.call_count)

    def test_legacy_final_results_are_strictly_bound_to_record_platform_and_completed_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "rec_source"
            run_dir.mkdir()
            checkpoint = {
                "record_id": "rec_source", "stage": "finalized", "version": 1,
                "stages": {
                    "prepared": {"legacy": "opaque"},
                    "evidence_validated": {"legacy": True},
                    "finalized": {"result_count": 1, "completed": [json.dumps(["SKU", "shein-us", "https://us.shein.com/item-p-1.html"])]},
                },
            }
            (run_dir / "checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
            bad_outputs = (
                {"task_record_id": "rec_other", "platform": "shein-us", "result_count": 1, "results": [{}]},
                {"task_record_id": "rec_source", "platform": "unknown", "result_count": 1, "results": [{}]},
                {"task_record_id": "rec_source", "platform": "shein-us", "result_count": 1, "results": []},
                {"task_record_id": "rec_source", "platform": "shein-us", "result_count": 1, "results": ["arbitrary"]},
            )
            for output in bad_outputs:
                with self.subTest(output=output):
                    (run_dir / "final-results.json").write_text(json.dumps(output), encoding="utf-8")
                    with self.assertRaises(WorkflowError):
                        finalize(run_dir, lark_client=FakeLark())

    def test_legacy_final_results_reject_off_platform_urls_and_duplicate_rows(self):
        def candidate(identity: str, url: str) -> dict[str, object]:
            return {
                "identity": identity, "platform": "shein-us", "title": "Dress",
                "canonical_url": url, "query_hits": ["mini dress"],
                "earliest_organic_rank": 1, "earliest_ad_rank": None,
                "sold_display": "500 sold", "sold_value": 500,
                "reviews_display": "100 reviews", "reviews_value": 100,
                "rating_display": "4.5", "rating_value": 4.5,
                "match_level": "高度相似", "visual_features": ["puff sleeve"],
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "rec_source"
            run_dir.mkdir()
            url = "https://us.shein.com/dress-p-1.html"
            cases = (
                (
                    [candidate("shein-us:https://evil.example/phish", "https://evil.example/phish")],
                    [["SKU-A", "shein-us", "https://evil.example/phish"]],
                ),
                (
                    [candidate("shein-us:1", url), candidate("shein-us:2", url)],
                    [["SKU-A", "shein-us", url], ["SKU-B", "shein-us", url]],
                ),
            )
            for results, completed in cases:
                with self.subTest(results=results):
                    (run_dir / "checkpoint.json").write_text(json.dumps({
                        "record_id": "rec_source", "stage": "finalized", "version": 1,
                        "stages": {
                            "prepared": {"legacy": "opaque"},
                            "evidence_validated": {"legacy": True},
                            "finalized": {"result_count": len(results), "completed": [json.dumps(value) for value in completed]},
                        },
                    }), encoding="utf-8")
                    (run_dir / "final-results.json").write_text(json.dumps({
                        "task_record_id": "rec_source", "platform": "shein-us",
                        "result_count": len(results), "results": results,
                    }), encoding="utf-8")
                    with self.assertRaises(WorkflowError):
                        finalize(run_dir, lark_client=FakeLark())

    def test_legacy_final_results_accept_one_exact_canonical_completed_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "rec_source"
            run_dir.mkdir()
            url = "https://us.shein.com/dress-p-1.html"
            result = {
                "identity": "shein-us:1", "platform": "shein-us", "title": "Dress",
                "canonical_url": url, "query_hits": ["mini dress"],
                "earliest_organic_rank": 1, "earliest_ad_rank": None,
                "sold_display": "500 sold", "sold_value": 500,
                "reviews_display": "100 reviews", "reviews_value": 100,
                "rating_display": "4.5", "rating_value": 4.5,
                "match_level": "高度相似", "visual_features": ["puff sleeve"],
            }
            (run_dir / "checkpoint.json").write_text(json.dumps({
                "record_id": "rec_source", "stage": "finalized", "version": 1,
                "stages": {
                    "prepared": {"legacy": "opaque"}, "evidence_validated": {"legacy": True},
                    "finalized": {"result_count": 1, "completed": [json.dumps(["SKU-A", "shein-us", url])]},
                },
            }), encoding="utf-8")
            (run_dir / "final-results.json").write_text(json.dumps({
                "task_record_id": "rec_source", "platform": "shein-us", "result_count": 1,
                "results": [result],
            }), encoding="utf-8")
            self.assertIn("Already finalized 1", finalize(run_dir, lark_client=FakeLark()))


class FinalizeTests(unittest.TestCase):
    def _validated(self, root: Path, limit=2):
        prepare_run(root, limit=limit)
        validate_evidence(
            root / "rec_source", evidence_fixture(root, "shein-evidence.json"),
            clock=frozen_clock(),
        )

    def _digestless_finalized_one(self, root: Path) -> tuple[Path, Path, Path]:
        """Build a valid pre-digest finalized v2 run with one selected result."""
        prepare_run(root, limit=1)
        evidence = load_fixture("shein-evidence.json")
        evidence["details"] = evidence["details"][:1]
        evidence_path = root / "one-result-evidence.json"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        validate_evidence(root / "rec_source", evidence_path, clock=frozen_clock())
        finalize(
            root / "rec_source",
            lark_client=FakeLark([task(limit=1)]),
            clock=frozen_clock(),
        )
        run_dir = root / "rec_source"
        checkpoint_path = run_dir / "checkpoint.json"
        results_path = run_dir / "final-results.json"
        legacy = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        legacy["stages"]["evidence_validated"].pop("evidence_digest")
        checkpoint_path.write_text(json.dumps(legacy), encoding="utf-8")
        lock_path = run_dir / ".finalize.lock"
        lock_path.unlink()
        return run_dir, checkpoint_path, results_path

    def test_dry_run_writes_local_results_only_and_does_not_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            lark = FakeLark()
            summary = finalize(root / "rec_source", dry_run=True, lark_client=lark)
            self.assertIn("2 result", summary)
            self.assertEqual([], lark.events)
            output = json.loads((root / "rec_source" / "final-results.json").read_text(encoding="utf-8"))
            self.assertEqual(2, len(output["results"]))
            self.assertEqual("evidence_validated", CheckpointStore(root).load("rec_source")["stage"])

    def test_live_finalize_is_ranked_and_marks_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            lark = FakeLark()
            finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            writes = [event for event in lark.events if event[0] == "write"]
            self.assertEqual(["shein-us:100001", "shein-us:100002"], [event[1] for event in writes])
            self.assertEqual(["validate", ("list", "rec_source")], lark.events[:2])
            self.assertEqual(("status", "rec_source", "成功", False), lark.events[-1])
            self.assertEqual("finalized", CheckpointStore(root).load("rec_source")["stage"])

    def test_partial_operational_failure_preserves_pending_status_and_resumes_without_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            first = FakeLark()
            first.fail_write_at = 2
            with self.assertRaises(WorkflowError) as raised:
                finalize(root / "rec_source", lark_client=first, clock=frozen_clock())
            self.assertNotIn("top-secret", str(raised.exception))
            self.assertFalse(any(event[0] == "status" for event in first.events if isinstance(event, tuple)))
            second = FakeLark()
            finalize(root / "rec_source", lark_client=second, clock=frozen_clock())
            writes = [event[1] for event in second.events if event[0] == "write"]
            self.assertEqual(["shein-us:100002"], writes)

    def test_checkpoint_api_rejects_forged_first_complete_progress_then_finalize_writes_each_row(self):
        """The first durable progress save cannot skip unperformed Base writes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            store = CheckpointStore(root)
            checkpoint = store.load("rec_source")
            evidence = copy.deepcopy(checkpoint["stages"]["evidence_validated"])
            prepared = checkpoint["stages"]["prepared"]
            saved_task = Task.from_dict(prepared["task"])
            selected = select_results(
                saved_task,
                [VerifiedCandidate.from_dict(value) for value in evidence["candidates"]],
            )
            intended = [
                json.dumps(
                    [saved_task.sku, saved_task.platform.value, value.canonical_url],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                for value in selected
            ]
            evidence["write_progress"] = {
                "intended": intended,
                "completed": intended,
                "writes_complete": True,
            }
            with self.assertRaisesRegex(CheckpointError, "progress"):
                store.save_stage("rec_source", "evidence_validated", evidence)

            lark = FakeLark()
            finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            self.assertEqual(
                ["shein-us:100001", "shein-us:100002"],
                [event[1] for event in lark.events if event[0] == "write"],
            )

    def test_digestless_v2_finalized_checkpoint_is_replayed_read_only(self):
        """A valid old finalized payload returns without digest migration or Base calls."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            initial = FakeLark()
            finalize(root / "rec_source", lark_client=initial, clock=frozen_clock())
            run_dir = root / "rec_source"
            checkpoint_path = run_dir / "checkpoint.json"
            legacy = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            legacy["stages"]["evidence_validated"].pop("evidence_digest")
            checkpoint_path.write_text(json.dumps(legacy), encoding="utf-8")
            before_checkpoint = checkpoint_path.read_bytes()
            before_results = (run_dir / "final-results.json").read_bytes()
            lock_path = run_dir / ".finalize.lock"
            lock_path.unlink()

            lark = FakeLark()
            summary = finalize(run_dir, lark_client=lark, clock=frozen_clock())
            self.assertIn("Already finalized 2", summary)
            self.assertEqual([], lark.events)
            self.assertEqual(before_checkpoint, checkpoint_path.read_bytes())
            self.assertEqual(before_results, (run_dir / "final-results.json").read_bytes())
            self.assertFalse(lock_path.exists())

    def test_digestless_v2_finalized_result_counts_require_exact_nonnegative_ints(self):
        """Bool and integral-looking floats cannot impersonate persisted counts."""
        count_pairs = (
            (True, 1),
            (1, True),
            (True, True),
            (1.0, 1),
            (1, 1.0),
            (1.0, 1.0),
        )
        for checkpoint_count, output_count in count_pairs:
            with self.subTest(
                checkpoint_count=checkpoint_count, output_count=output_count,
            ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run_dir, checkpoint_path, results_path = self._digestless_finalized_one(root)
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                checkpoint["stages"]["finalized"]["result_count"] = checkpoint_count
                checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
                results = json.loads(results_path.read_text(encoding="utf-8"))
                results["result_count"] = output_count
                results_path.write_text(json.dumps(results), encoding="utf-8")
                before_checkpoint = checkpoint_path.read_bytes()
                before_results = results_path.read_bytes()
                lark = FakeLark()

                with self.assertRaisesRegex(WorkflowError, "validated checkpoint"):
                    finalize(run_dir, lark_client=lark, clock=frozen_clock())

                self.assertEqual([], lark.events)
                self.assertEqual(before_checkpoint, checkpoint_path.read_bytes())
                self.assertEqual(before_results, results_path.read_bytes())
                self.assertFalse((run_dir / ".finalize.lock").exists())

    def test_digestless_v2_finalized_evidence_counts_require_exact_nonnegative_ints(self):
        """Replay count comparisons do not accept bool/int or float/int coercion."""
        mutations = (
            ("observation_count", 90.0),
            ("recurring_count", 2.0),
            ("detail_count", True),
            ("detail_count", 1.0),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run_dir, checkpoint_path, results_path = self._digestless_finalized_one(root)
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                checkpoint["stages"]["evidence_validated"][field] = value
                checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
                before_checkpoint = checkpoint_path.read_bytes()
                before_results = results_path.read_bytes()
                lark = FakeLark()

                with self.assertRaisesRegex(WorkflowError, "validated checkpoint"):
                    finalize(run_dir, lark_client=lark, clock=frozen_clock())

                self.assertEqual([], lark.events)
                self.assertEqual(before_checkpoint, checkpoint_path.read_bytes())
                self.assertEqual(before_results, results_path.read_bytes())
                self.assertFalse((run_dir / ".finalize.lock").exists())

    def test_digestless_v2_finalized_checkpoint_with_changed_results_fails_read_only(self):
        """The old-finalized read path requires exact persisted final results."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            finalize(root / "rec_source", lark_client=FakeLark(), clock=frozen_clock())
            run_dir = root / "rec_source"
            checkpoint_path = run_dir / "checkpoint.json"
            legacy = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            legacy["stages"]["evidence_validated"].pop("evidence_digest")
            checkpoint_path.write_text(json.dumps(legacy), encoding="utf-8")
            results_path = run_dir / "final-results.json"
            results = json.loads(results_path.read_text(encoding="utf-8"))
            results["result_count"] = 1
            results_path.write_text(json.dumps(results), encoding="utf-8")
            lock_path = run_dir / ".finalize.lock"
            lock_path.unlink()

            lark = FakeLark()
            with self.assertRaisesRegex(WorkflowError, "validated checkpoint"):
                finalize(run_dir, lark_client=lark, clock=frozen_clock())
            self.assertEqual([], lark.events)
            self.assertFalse(lock_path.exists())

    def test_success_then_final_checkpoint_failure_reconciles_without_duplicate_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            lark = FakeLark()
            original = CheckpointStore.save_stage
            failed = False

            def fail_first_final_checkpoint(store, record_id, stage, payload):
                nonlocal failed
                if stage == "finalized" and not failed:
                    failed = True
                    raise RuntimeError("final checkpoint crash")
                return original(store, record_id, stage, payload)

            with patch(
                "scripts.workflow.CheckpointStore.save_stage",
                autospec=True,
                side_effect=fail_first_final_checkpoint,
            ):
                with self.assertRaises(WorkflowError):
                    finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())

            checkpoint = CheckpointStore(root).load("rec_source")
            progress = checkpoint["stages"]["evidence_validated"]["write_progress"]
            self.assertTrue(progress["writes_complete"])
            self.assertEqual("成功", lark.status)
            first_write_count = len([event for event in lark.events if event[0] == "write"])

            summary = finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())

            self.assertIn("Reconciled", summary)
            self.assertEqual(first_write_count, len([event for event in lark.events if event[0] == "write"]))
            self.assertEqual(1, len([event for event in lark.events if event[0] == "status"]))
            self.assertEqual("finalized", CheckpointStore(root).load("rec_source")["stage"])

    def test_success_reconciliation_supports_legacy_complete_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            lark = FakeLark()
            original = CheckpointStore.save_stage

            def fail_final_checkpoint(store, record_id, stage, payload):
                if stage == "finalized":
                    raise RuntimeError("legacy crash window")
                return original(store, record_id, stage, payload)

            with patch(
                "scripts.workflow.CheckpointStore.save_stage",
                autospec=True,
                side_effect=fail_final_checkpoint,
            ):
                with self.assertRaises(WorkflowError):
                    finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())

            store = CheckpointStore(root)
            checkpoint = store.load("rec_source")
            evidence = dict(checkpoint["stages"]["evidence_validated"])
            legacy_progress = dict(evidence["write_progress"])
            legacy_progress.pop("writes_complete")
            evidence["write_progress"] = legacy_progress
            # A direct file change represents an untrusted persisted payload;
            # CheckpointStore itself rejects semantic same-stage replacement.
            checkpoint["stages"]["evidence_validated"] = evidence
            (root / "rec_source" / "checkpoint.json").write_text(
                json.dumps(checkpoint), encoding="utf-8",
            )

            summary = finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            self.assertIn("Reconciled", summary)
            self.assertEqual("finalized", store.load("rec_source")["stage"])

    def test_success_reconciliation_fails_closed_if_an_intended_result_cannot_be_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            lark = FakeLark()
            original = CheckpointStore.save_stage

            def fail_final_checkpoint(store, record_id, stage, payload):
                if stage == "finalized":
                    raise RuntimeError("final checkpoint crash")
                return original(store, record_id, stage, payload)

            with patch(
                "scripts.workflow.CheckpointStore.save_stage",
                autospec=True,
                side_effect=fail_final_checkpoint,
            ):
                with self.assertRaises(WorkflowError):
                    finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            lark.results.remove("shein-us:100002")

            with self.assertRaisesRegex(WorkflowError, "verification"):
                finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            self.assertEqual("evidence_validated", CheckpointStore(root).load("rec_source")["stage"])
            self.assertEqual(1, len([event for event in lark.events if event[0] == "status"]))

    def test_zero_qualifying_results_is_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            value = load_fixture("shein-evidence.json")
            for detail in value["details"]:
                detail.update({
                    "status": "rejected",
                    "reason": "threshold_failure",
                    "reviews_display": "1",
                    "match_level": None,
                    "visual_features": None,
                })
            path = root / "zero.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            validate_evidence(root / "rec_source", path, clock=frozen_clock())
            lark = FakeLark()
            summary = finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            self.assertIn("0 result", summary)
            self.assertFalse(any(event[0] == "write" for event in lark.events))
            self.assertIn(("status", "rec_source", "成功", False), lark.events)

    def test_absent_evidence_has_exact_chrome_instruction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_only(root)
            with self.assertRaises(WorkflowError) as raised:
                finalize(root / "rec_source", lark_client=FakeLark())
            self.assertEqual(NEXT_STEP, str(raised.exception))

    def test_evidence_is_immutable_after_external_write_before_progress_save(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            lark = FakeLark()
            original = CheckpointStore.save_stage

            def fail_after_external_write(store, record_id, stage, payload):
                progress = payload.get("write_progress") if isinstance(payload, dict) else None
                if stage == "evidence_validated" and isinstance(progress, dict) and progress.get("completed"):
                    raise RuntimeError("checkpoint crash")
                return original(store, record_id, stage, payload)

            with patch("scripts.workflow.CheckpointStore.save_stage", autospec=True, side_effect=fail_after_external_write):
                with self.assertRaises(WorkflowError):
                    finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            self.assertEqual(1, len([event for event in lark.events if event[0] == "write"]))
            changed = load_fixture("shein-evidence.json")
            changed["details"][0]["title"] = "Changed detail title"
            changed_path = root / "changed.json"
            changed_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "replace"):
                validate_evidence(root / "rec_source", changed_path, clock=frozen_clock())

    def test_live_finalize_revalidates_exact_pending_task_before_any_mutation(self):
        mutations = (
            Task(**{**task().__dict__, "sku": "CHANGED"}),
            Task(**{**task().__dict__, "image_token": "changed-token"}),
            Task(**{**task().__dict__, "result_limit": 1}),
        )
        for changed in mutations:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._validated(root)
                lark = FakeLark([changed])
                with self.assertRaisesRegex(WorkflowError, "task|pending|changed"):
                    finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
                self.assertEqual(["validate", ("list", "rec_source")], lark.events)

    def test_stale_evidence_is_retryable_and_causes_no_base_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            lark = FakeLark()
            with self.assertRaisesRegex(WorkflowError, "stale"):
                finalize(
                    root / "rec_source", lark_client=lark,
                    clock=frozen_clock("2026-08-21T11:00:01+00:00"),
                    evidence_max_age_seconds=3600,
                )
            self.assertEqual(["validate", ("list", "rec_source")], lark.events)

    def test_exact_evidence_refresh_after_progress_preserves_completed_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            first = FakeLark()
            first.fail_write_at = 2
            with self.assertRaises(WorkflowError):
                finalize(root / "rec_source", lark_client=first, clock=frozen_clock())
            validate_evidence(
                root / "rec_source", evidence_fixture(root, "shein-evidence.json"),
                clock=frozen_clock("2026-08-21T10:10:00+00:00"),
            )
            refreshed = CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]
            self.assertEqual(1, len(refreshed["write_progress"]["completed"]))
            second = FakeLark()
            finalize(
                root / "rec_source", lark_client=second,
                clock=frozen_clock("2026-08-21T10:10:00+00:00"),
            )
            self.assertEqual(
                ["shein-us:100002"],
                [event[1] for event in second.events if event[0] == "write"],
            )

    def test_changed_direct_resolution_cannot_replace_progress_held_under_the_record_lock(self):
        """Catch a refresh that discards durable Base-write progress for changed seeds."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            first = FakeLark()
            first.fail_write_at = 2
            with self.assertRaises(WorkflowError):
                finalize(root / "rec_source", lark_client=first, clock=frozen_clock())
            before = copy.deepcopy(
                CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]
            )

            checkpoint_path = root / "rec_source" / "checkpoint.json"
            changed = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            changed["stages"]["queries_resolved"]["queries"] = [
                "mini dress", "cocktail dress", "puff sleeve mini dress",
            ]
            checkpoint_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "resolved queries checkpoint"):
                validate_evidence(
                    root / "rec_source", evidence_fixture(root, "shein-evidence.json"), clock=frozen_clock(),
                )

            after = CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]
            self.assertEqual(before["write_progress"], after["write_progress"])
            self.assertEqual(before["query_provenance"], after["query_provenance"])

    def test_finalize_rejects_tampered_direct_query_provenance_before_base_mutation(self):
        """Catch a finalized reader that trusts copied queries without Ark binding."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            store = CheckpointStore(root)
            checkpoint = store.load("rec_source")
            evidence = dict(checkpoint["stages"]["evidence_validated"])
            provenance = dict(evidence["query_provenance"])
            provenance["queries"] = ["mini dress", "puff sleeve party dress", "cocktail dress"]
            evidence["query_provenance"] = provenance
            checkpoint["stages"]["evidence_validated"] = evidence
            (root / "rec_source" / "checkpoint.json").write_text(
                json.dumps(checkpoint), encoding="utf-8",
            )

            lark = FakeLark()
            with self.assertRaisesRegex(WorkflowError, "validated checkpoint"):
                finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            self.assertEqual([], lark.events)

    def test_refresh_rejects_existing_tampered_provenance_even_before_write_progress(self):
        """Catch refresh logic that silently repairs a forged stored provenance."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            store = CheckpointStore(root)
            checkpoint = store.load("rec_source")
            evidence = dict(checkpoint["stages"]["evidence_validated"])
            provenance = dict(evidence["query_provenance"])
            provenance["source"] = "autocomplete"
            evidence["query_provenance"] = provenance
            checkpoint["stages"]["evidence_validated"] = evidence
            (root / "rec_source" / "checkpoint.json").write_text(
                json.dumps(checkpoint), encoding="utf-8",
            )

            with self.assertRaisesRegex(WorkflowError, "validated checkpoint"):
                validate_evidence(
                    root / "rec_source", evidence_fixture(root, "shein-evidence.json"),
                    clock=frozen_clock("2026-08-21T10:01:00+00:00"),
                )
            self.assertEqual(
                provenance,
                CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"]["query_provenance"],
            )

    def test_legacy_v2_evidence_without_digest_migrates_progress_without_duplicate_base_writes(self):
        """Catch a digest rollout that invalidates a resumable old partial write."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            first = FakeLark()
            first.fail_write_at = 2
            with self.assertRaises(WorkflowError):
                finalize(root / "rec_source", lark_client=first, clock=frozen_clock())

            checkpoint_path = root / "rec_source" / "checkpoint.json"
            legacy = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            legacy["stages"]["evidence_validated"].pop("evidence_digest")
            checkpoint_path.write_text(json.dumps(legacy), encoding="utf-8")

            second = FakeLark()
            finalize(root / "rec_source", lark_client=second, clock=frozen_clock())
            self.assertEqual(
                ["shein-us:100002"],
                [event[1] for event in second.events if event[0] == "write"],
            )
            migrated = CheckpointStore(root).load("rec_source")
            self.assertIn("evidence_digest", migrated["stages"]["evidence_validated"])

    def test_legacy_v2_missing_digest_with_changed_core_fails_before_results_or_base_write(self):
        """Digest migration is a replay check, never a core-replacement bypass."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            checkpoint_path = root / "rec_source" / "checkpoint.json"
            legacy = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            evidence = legacy["stages"]["evidence_validated"]
            evidence.pop("evidence_digest")
            evidence["observation_count"] = 91
            checkpoint_path.write_text(json.dumps(legacy), encoding="utf-8")

            lark = FakeLark()
            with self.assertRaisesRegex(WorkflowError, "validated checkpoint"):
                finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            self.assertFalse((root / "rec_source" / "final-results.json").exists())
            self.assertEqual([], lark.events)
            self.assertNotIn(
                "evidence_digest",
                CheckpointStore(root).load("rec_source")["stages"]["evidence_validated"],
            )

    def test_finalize_replays_stored_evidence_before_any_result_or_base_write(self):
        """Catch finalization trusting copied candidate data instead of canonical evidence replay."""
        mutations = {
            "unbound Ark-seed query hit": lambda value: value["candidates"][0].update({
                "query_hits": ["mini dress", "puff sleeve mini dress"],
            }),
            "source-color feature": lambda value: value["evidence"]["details"][0].update({
                "visual_features": ["red embroidery"],
            }),
            "observation": lambda value: value["evidence"]["queries"][0]["observations"][0].update({
                "title": "Forged visible card",
            }),
            "detail": lambda value: value["evidence"]["details"][0].update({
                "title": "Forged detail title",
            }),
            "count": lambda value: value.update({"observation_count": 91}),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prepare_only(root)
                source = load_fixture("shein-evidence.json")
                source_path = root / "source.json"
                source_path.write_text(json.dumps(source), encoding="utf-8")
                validate_evidence(root / "rec_source", source_path, clock=frozen_clock())

                store = CheckpointStore(root)
                checkpoint = store.load("rec_source")
                evidence = copy.deepcopy(checkpoint["stages"]["evidence_validated"])
                mutate(evidence)
                # Exercise the read/replay boundary after an attacker bypasses
                # the checkpoint API; finalization must still fail closed.
                checkpoint["stages"]["evidence_validated"] = evidence
                (root / "rec_source" / "checkpoint.json").write_text(
                    json.dumps(checkpoint), encoding="utf-8",
                )

                lark = FakeLark()
                with self.assertRaisesRegex(WorkflowError, "validated checkpoint"):
                    finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
                self.assertFalse((root / "rec_source" / "final-results.json").exists())
                self.assertEqual([], lark.events)

    def test_evidence_refresh_cannot_race_finalization_and_status_failure_remains_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._validated(root)
            lark = FakeLark()
            lark.fail_status = True
            lark.status_started = threading.Event()
            lark.status_release = threading.Event()
            failures: list[Exception] = []

            def run_finalize():
                try:
                    finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
                except Exception as exc:
                    failures.append(exc)

            worker = threading.Thread(target=run_finalize)
            worker.start()
            self.assertTrue(lark.status_started.wait(5))
            with self.assertRaisesRegex(WorkflowError, "lock"):
                validate_evidence(
                    root / "rec_source", FIXTURES / "shein-evidence.json",
                    clock=frozen_clock("2026-08-21T10:01:00+00:00"),
                )
            lark.status_release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(1, len(failures))

            checkpoint = CheckpointStore(root).load("rec_source")
            progress = checkpoint["stages"]["evidence_validated"]["write_progress"]
            self.assertTrue(progress["writes_complete"])
            self.assertEqual(progress["intended"], progress["completed"])

            writes_before_retry = len([event for event in lark.events if event[0] == "write"])
            lark.fail_status = False
            lark.status_started = None
            lark.status_release = None
            finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
            self.assertEqual(writes_before_retry, len([event for event in lark.events if event[0] == "write"]))
            self.assertEqual("finalized", CheckpointStore(root).load("rec_source")["stage"])

    def test_concurrent_live_finalizers_are_serialized_by_record_lock(self):
        if "spawn" not in multiprocessing.get_all_start_methods():
            self.skipTest("spawn multiprocessing context is required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root, limit=1)
            value = load_fixture("shein-evidence.json")
            value["details"] = value["details"][:1]
            evidence_path = root / "one.json"
            evidence_path.write_text(json.dumps(value), encoding="utf-8")
            validate_evidence(root / "rec_source", evidence_path, clock=frozen_clock())
            event_path = root / "external-writes.log"
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            processes = [
                context.Process(
                    target=concurrent_finalize,
                    args=(str(root / "rec_source"), str(event_path), start),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(5)
                self.assertEqual(0, process.exitcode)
            self.assertEqual(["write"], event_path.read_text(encoding="utf-8").splitlines())


class CliTests(unittest.TestCase):
    def test_resolve_queries_cli_is_labeled_legacy_compatibility_only(self):
        help_text = workflow_module._parser().format_help()
        self.assertIn(
            "Legacy version-2 autocomplete checkpoint compatibility only",
            " ".join(help_text.split()),
        )

    def test_main_prints_sanitized_workflow_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_run(root)
            from contextlib import redirect_stderr
            import io
            stream = io.StringIO()
            with redirect_stderr(stream):
                code = main(["finalize", "--run-dir", str(root / "rec_source")])
            self.assertEqual(1, code)
            self.assertIn("Use the selected Chrome session", stream.getvalue())

    def test_direct_script_help(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "workflow.py"), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("validate-evidence", completed.stdout)


if __name__ == "__main__":
    unittest.main()
