import errno
import copy
import json
import os
import stat
import tempfile
import threading
import traceback
import unittest
from pathlib import Path
from unittest import mock

from scripts.checkpoint import CheckpointError, CheckpointStore, STAGES


def evidence_payload(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "query_provenance": {
            "source": "ark_seeds", "queries": ["one", "two", "three"],
        },
        "evidence": {"queries": []},
        "candidates": [],
        "observation_count": 90,
        "recurring_count": 2,
        "detail_count": 2,
        "screenshot_manifest": [{"id": "proof-1"}],
        "screenshot_count": 1,
        "evidence_digest": "a" * 64,
        "validated_at": "2026-08-21T10:00:00Z",
    }
    value.update(changes)
    return value


class CheckpointBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "checkpoints"
        self.store = CheckpointStore(self.root)

    def test_initial_save_creates_prepared_checkpoint_and_load_returns_fresh_data(self) -> None:
        path = self.store.save_stage("recABC123", "prepared", {"query": "dress"})

        self.assertEqual(self.root / "recABC123" / "checkpoint.json", path)
        self.assertEqual(
            {
                "record_id": "recABC123",
                "stage": "prepared",
                "stages": {"prepared": {"query": "dress"}},
                "version": 2,
            },
            self.store.load("recABC123"),
        )
        first = self.store.load("recABC123")
        assert first is not None
        first["stage"] = "finalized"
        self.assertEqual("prepared", self.store.load("recABC123")["stage"])  # type: ignore[index]

    def test_all_transitions_preserve_earlier_stage_payloads(self) -> None:
        evidence = evidence_payload()
        self.store.save_stage("recFlow1", "prepared", {"queries": ["one", "two"]})
        self.store.save_stage("recFlow1", "queries_resolved", {"queries": ["one", "two", "three"]})
        self.store.save_stage("recFlow1", "evidence_validated", evidence)
        self.store.save_stage("recFlow1", "finalized", {"rows": 1})

        self.assertEqual(
            {
                "prepared": {"queries": ["one", "two"]},
                "queries_resolved": {"queries": ["one", "two", "three"]},
                "evidence_validated": evidence,
                "finalized": {"rows": 1},
            },
            self.store.load("recFlow1")["stages"],  # type: ignore[index]
        )

    def test_same_stage_semantic_replacement_is_rejected_for_every_checkpoint_stage(self) -> None:
        self.store.save_stage("recReplace1", "prepared", {"attempt": 1})
        with self.assertRaisesRegex(CheckpointError, "immutable"):
            self.store.save_stage("recReplace1", "prepared", {"attempt": 2})
        self.store.save_stage("recReplace1", "queries_resolved", {"queries": ["one", "two", "three"]})
        with self.assertRaisesRegex(CheckpointError, "immutable"):
            self.store.save_stage("recReplace1", "queries_resolved", {"queries": ["one", "two", "four"]})
        evidence = evidence_payload()
        self.store.save_stage("recReplace1", "evidence_validated", evidence)
        with self.assertRaisesRegex(CheckpointError, "immutable"):
            self.store.save_stage(
                "recReplace1", "evidence_validated",
                evidence_payload(evidence_digest="b" * 64),
            )
        self.store.save_stage("recReplace1", "finalized", {"rows": 1})
        with self.assertRaisesRegex(CheckpointError, "immutable"):
            self.store.save_stage("recReplace1", "finalized", {"rows": 2})

        loaded = self.store.load("recReplace1")
        assert loaded is not None
        self.assertEqual({"attempt": 1}, loaded["stages"]["prepared"])
        self.assertEqual(evidence, loaded["stages"]["evidence_validated"])

    def test_evidence_current_stage_allows_only_monotonic_metadata_and_progress(self) -> None:
        self.store.save_stage("recEvidence1", "prepared", {"prepared": True})
        self.store.save_stage("recEvidence1", "queries_resolved", {"resolved": True})
        core = {
            "autocomplete_provenance": {"queries": ["one", "two", "three"]},
            "evidence": {"queries": []},
            "candidates": [],
            "observation_count": 90,
            "recurring_count": 2,
            "detail_count": 2,
            "screenshot_manifest": [{"id": "proof-1"}],
            "screenshot_count": 1,
            "evidence_digest": "a" * 64,
        }
        initial = {**core, "validated_at": "2026-08-21T10:00:00Z"}
        self.store.save_stage("recEvidence1", "evidence_validated", initial)
        refreshed = {**core, "validated_at": "2026-08-21T10:01:00Z"}
        self.store.save_stage("recEvidence1", "evidence_validated", refreshed)
        forged_first_progress = {
            **refreshed,
            "write_progress": {
                "intended": ["row-1", "row-2"],
                "completed": ["row-1", "row-2"],
                "writes_complete": True,
            },
        }
        with self.assertRaisesRegex(CheckpointError, "progress"):
            self.store.save_stage(
                "recEvidence1", "evidence_validated", forged_first_progress,
            )

    def test_direct_seed_evidence_allows_timestamp_refresh_without_replacing_provenance(self) -> None:
        self.store.save_stage("recDirectEvidence1", "prepared", {"prepared": True})
        self.store.save_stage("recDirectEvidence1", "queries_resolved", {
            "source": "ark_seeds", "queries": ["one", "two", "three"],
        })
        core = {
            "query_provenance": {"source": "ark_seeds", "queries": ["one", "two", "three"]},
            "evidence": {"queries": []},
            "candidates": [],
            "observation_count": 90,
            "recurring_count": 2,
            "detail_count": 2,
            "screenshot_manifest": [{"id": "proof-1"}],
            "screenshot_count": 1,
            "evidence_digest": "a" * 64,
        }
        self.store.save_stage(
            "recDirectEvidence1", "evidence_validated",
            {**core, "validated_at": "2026-08-21T10:00:00Z"},
        )
        self.store.save_stage(
            "recDirectEvidence1", "evidence_validated",
            {
                **core,
                "validated_at": "2026-08-21T10:01:00Z",
            },
        )

    def test_screenshotless_evidence_core_is_immutable(self) -> None:
        self.store.save_stage("recLegacyEvidence1", "prepared", {"prepared": True})
        self.store.save_stage(
            "recLegacyEvidence1", "queries_resolved",
            {"source": "ark_seeds", "queries": ["one", "two", "three"]},
        )
        screenshotless = {
            "query_provenance": {
                "source": "ark_seeds", "queries": ["one", "two", "three"],
            },
            "evidence": {"queries": []},
            "candidates": [],
            "observation_count": 90,
            "recurring_count": 2,
            "detail_count": 2,
            "evidence_digest": "a" * 64,
            "validated_at": "2026-08-21T10:00:00Z",
        }
        checkpoint_path = self.root / "recLegacyEvidence1" / "checkpoint.json"
        checkpoint_path.write_text(json.dumps({
            "record_id": "recLegacyEvidence1",
            "stage": "evidence_validated",
            "stages": {
                "prepared": {"prepared": True},
                "queries_resolved": {
                    "source": "ark_seeds", "queries": ["one", "two", "three"],
                },
                "evidence_validated": screenshotless,
            },
            "version": 2,
        }), encoding="utf-8")
        self.assertEqual(
            screenshotless,
            self.store.load("recLegacyEvidence1")["stages"]["evidence_validated"],
        )
        with self.assertRaisesRegex(CheckpointError, "immutable"):
            self.store.save_stage(
                "recLegacyEvidence1", "evidence_validated",
                {**screenshotless, "validated_at": "2026-08-21T10:01:00Z"},
            )

    def test_new_evidence_stage_requires_paired_screenshot_fields(self) -> None:
        core = {
            "query_provenance": {
                "source": "ark_seeds", "queries": ["one", "two", "three"],
            },
            "evidence": {"queries": []},
            "candidates": [],
            "observation_count": 90,
            "recurring_count": 2,
            "detail_count": 2,
            "screenshot_manifest": [{"id": "proof-1"}],
            "screenshot_count": 1,
            "evidence_digest": "a" * 64,
            "validated_at": "2026-08-21T10:00:00Z",
        }
        for index, missing in enumerate(
            ({"screenshot_manifest", "screenshot_count"}, {"screenshot_manifest"}, {"screenshot_count"}),
            start=1,
        ):
            with self.subTest(missing=missing):
                record_id = f"recMissingScreenshots{index}"
                self.store.save_stage(record_id, "prepared", {"prepared": True})
                self.store.save_stage(
                    record_id, "queries_resolved",
                    {"source": "ark_seeds", "queries": ["one", "two", "three"]},
                )
                payload = {key: value for key, value in core.items() if key not in missing}
                with self.assertRaisesRegex(CheckpointError, "screenshot"):
                    self.store.save_stage(record_id, "evidence_validated", payload)
                loaded = self.store.load(record_id)
                assert loaded is not None
                self.assertEqual("queries_resolved", loaded["stage"])
                self.assertNotIn("evidence_validated", loaded["stages"])

    def test_legacy_v1_is_read_only_only_after_the_exact_legacy_finalized_prefix(self) -> None:
        record_id = "recLegacy1"
        legacy = {
            "record_id": record_id,
            "stage": "finalized",
            "stages": {
                "prepared": {"obsolete": True},
                "evidence_validated": {"obsolete": True},
                "finalized": {"result_count": 0, "completed": []},
            },
            "version": 1,
        }
        path = self.root / record_id / "checkpoint.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(legacy, self.store.load(record_id))
        with self.assertRaisesRegex(CheckpointError, "read-only"):
            self.store.save_stage(record_id, "finalized", {"result_count": 0, "completed": []})

        legacy["stage"] = "evidence_validated"
        legacy["stages"].pop("finalized")
        path.write_text(json.dumps(legacy), encoding="utf-8")
        with self.assertRaises(CheckpointError):
            self.store.load(record_id)

    def test_legacy_finalized_completed_never_synthesizes_queries_resolved(self) -> None:
        record_id = "recLegacyCompleted1"
        path = self.root / record_id / "checkpoint.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "record_id": record_id, "stage": "finalized", "version": 1,
            "stages": {
                "prepared": {}, "evidence_validated": {},
                "finalized": {"result_count": 0, "completed": []},
            },
        }), encoding="utf-8")
        self.assertTrue(self.store.completed(record_id, "prepared"))
        self.assertTrue(self.store.completed(record_id, "evidence_validated"))
        self.assertTrue(self.store.completed(record_id, "finalized"))
        self.assertFalse(self.store.completed(record_id, "queries_resolved"))

    def test_completed_reports_stages_at_or_before_current_stage(self) -> None:
        self.assertFalse(self.store.completed("recStatus1", "prepared"))
        self.store.save_stage("recStatus1", "prepared", {})
        self.assertTrue(self.store.completed("recStatus1", "prepared"))
        self.assertFalse(self.store.completed("recStatus1", "evidence_validated"))
        self.store.save_stage("recStatus1", "queries_resolved", {})
        self.assertTrue(self.store.completed("recStatus1", "queries_resolved"))
        self.store.save_stage("recStatus1", "evidence_validated", evidence_payload())
        self.assertTrue(self.store.completed("recStatus1", "prepared"))
        self.assertTrue(self.store.completed("recStatus1", "evidence_validated"))
        self.assertFalse(self.store.completed("recStatus1", "finalized"))

    def test_save_normalizes_without_mutating_or_aliasing_caller_input(self) -> None:
        payload = {"nested": {"values": (1, 2)}, "labels": ["a"]}
        self.store.save_stage("recInput1", "prepared", payload)

        self.assertEqual({"nested": {"values": (1, 2)}, "labels": ["a"]}, payload)
        payload["nested"]["values"] = (9,)  # type: ignore[index]
        payload["labels"].append("b")  # type: ignore[union-attr]
        self.assertEqual(
            {"nested": {"values": [1, 2]}, "labels": ["a"]},
            self.store.load("recInput1")["stages"]["prepared"],  # type: ignore[index]
        )

    def test_json_bytes_are_deterministic_utf8_sorted_and_compact(self) -> None:
        path = self.store.save_stage(
            "recJson1", "prepared", {"z": "é", "a": {"b": True}},
        )

        expected = (
            '{"record_id":"recJson1","stage":"prepared","stages":'
            '{"prepared":{"a":{"b":true},"z":"é"}},"version":2}'
        ).encode("utf-8")
        self.assertEqual(expected, path.read_bytes())
        self.assertEqual(json.loads(expected), self.store.load("recJson1"))

    def test_checkpoint_size_limit_is_checked_before_reading_json(self) -> None:
        path = self.store.save_stage("recSize1", "prepared", {"value": "large"})
        self.assertGreater(path.stat().st_size, 8)
        with mock.patch("scripts.checkpoint.MAX_CHECKPOINT_BYTES", 8):
            with self.assertRaisesRegex(CheckpointError, "size"):
                self.store.load("recSize1")

    def test_checkpoint_descriptor_reader_caps_growth_and_handles_short_reads(self) -> None:
        path = self.store.save_stage("recGrow1", "prepared", {"value": "ok"})
        limit = path.stat().st_size + 1
        chunks = iter((b"x" * limit, b"x", b""))
        with mock.patch("scripts.checkpoint.MAX_CHECKPOINT_BYTES", limit), mock.patch(
            "scripts.checkpoint.os.read", side_effect=lambda _fd, _size: next(chunks)
        ):
            with self.assertRaisesRegex(CheckpointError, "size"):
                self.store.load("recGrow1")

        self.store.save_stage("recShort1", "prepared", {"value": "short reads"})
        real_read = os.read
        with mock.patch(
            "scripts.checkpoint.os.read",
            side_effect=lambda descriptor, size: real_read(descriptor, min(size, 3)),
        ):
            loaded = self.store.load("recShort1")
        self.assertEqual("short reads", loaded["stages"]["prepared"]["value"])


class CheckpointAtomicityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "checkpoints"
        self.store = CheckpointStore(self.root)

    def test_replace_failure_preserves_prior_checkpoint_and_removes_owned_temp(self) -> None:
        path = self.store.save_stage("recAtomic1", "prepared", {"attempt": 1})
        prior = path.read_bytes()

        with mock.patch("scripts.checkpoint.os.replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(CheckpointError):
                self.store.save_stage("recAtomic1", "prepared", {"attempt": 2})

        self.assertEqual(prior, path.read_bytes())
        self.assertEqual([path], list(path.parent.iterdir()))

    def test_short_writes_are_retried_until_the_complete_json_is_persisted(self) -> None:
        real_write = os.write
        write_sizes: list[int] = []

        def short_write(file_descriptor: int, data: bytes | memoryview) -> int:
            chunk = data[:3]
            write_sizes.append(len(chunk))
            return real_write(file_descriptor, chunk)

        with mock.patch("scripts.checkpoint.os.write", side_effect=short_write):
            path = self.store.save_stage(
                "recShort1", "prepared", {"description": "a sufficiently long payload"},
            )

        self.assertGreater(len(write_sizes), 3)
        self.assertEqual("a sufficiently long payload", json.loads(path.read_bytes())["stages"]["prepared"]["description"])

    @unittest.skipUnless(os.name == "posix", "POSIX permission modes required")
    def test_new_task_directory_and_checkpoint_use_private_modes(self) -> None:
        path = self.store.save_stage("recMode1", "prepared", {})

        self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    @unittest.skipUnless(os.name == "posix", "directory fsync observation requires POSIX")
    def test_file_and_task_directory_are_fsynced(self) -> None:
        real_fsync = os.fsync
        synced_types: list[str] = []

        def recording_fsync(file_descriptor: int) -> None:
            mode = os.fstat(file_descriptor).st_mode
            synced_types.append("directory" if stat.S_ISDIR(mode) else "file")
            real_fsync(file_descriptor)

        with mock.patch("scripts.checkpoint.os.fsync", side_effect=recording_fsync):
            self.store.save_stage("recSync1", "prepared", {"ok": True})

        self.assertEqual(["file", "directory"], synced_types)

    def test_concurrent_reader_observes_only_complete_prior_or_next_json(self) -> None:
        path = self.store.save_stage("recRace1", "prepared", {"generation": 0, "blob": "a" * 4096})
        allowed = {path.read_bytes()}

        failures: list[object] = []
        stop = threading.Event()

        def writer() -> None:
            try:
                for generation in range(1, 9):
                    self.store.save_stage(
                        "recRace1",
                        "prepared",
                        {"generation": 0, "blob": "a" * 4096},
                    )
            except BaseException as exc:
                failures.append(exc)
            finally:
                stop.set()

        thread = threading.Thread(target=writer)
        thread.start()
        while not stop.is_set():
            try:
                observed = path.read_bytes()
                json.loads(observed)
                if observed not in allowed:
                    failures.append(observed)
                    break
            except BaseException as exc:
                failures.append(exc)
                break
        thread.join()

        self.assertEqual([], failures)

    @unittest.skipUnless(
        os.open in os.supports_dir_fd and os.rename in os.supports_dir_fd,
        "descriptor-relative filesystem operations required",
    )
    def test_load_rejects_task_directory_swap_instead_of_following_attacker_symlink(self) -> None:
        record_id = "recSwapLoad1"
        task = self.root / record_id
        self.store.save_stage(record_id, "prepared", {"origin": "inside"})
        parked = Path(self.directory.name) / "parked-load-task"
        attacker = Path(self.directory.name) / "attacker-load-task"
        attacker.mkdir()
        attacker_envelope = {
            "record_id": record_id,
            "stage": "prepared",
            "stages": {"prepared": {"origin": "attacker"}},
            "version": 2,
        }
        attacker_checkpoint = attacker / "checkpoint.json"
        attacker_checkpoint.write_text(
            json.dumps(attacker_envelope, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        attacker_bytes = attacker_checkpoint.read_bytes()
        real_open = os.open
        swapped = False

        def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if not swapped and Path(os.fspath(path)).name == "checkpoint.json":
                task.rename(parked)
                task.symlink_to(attacker, target_is_directory=True)
                swapped = True
            kwargs = {} if dir_fd is None else {"dir_fd": dir_fd}
            return real_open(path, flags, mode, **kwargs)

        with mock.patch("scripts.checkpoint.os.open", side_effect=swapping_open):
            with self.assertRaises(CheckpointError):
                self.store.load(record_id)

        self.assertTrue(swapped)
        self.assertEqual(attacker_bytes, attacker_checkpoint.read_bytes())

    @unittest.skipUnless(
        os.open in os.supports_dir_fd and os.rename in os.supports_dir_fd,
        "descriptor-relative filesystem operations required",
    )
    def test_save_rejects_task_directory_swap_without_touching_attacker_or_leaking_temp(self) -> None:
        record_id = "recSwapSave1"
        task = self.root / record_id
        checkpoint = self.store.save_stage(record_id, "prepared", {"attempt": 1})
        prior = checkpoint.read_bytes()
        parked = Path(self.directory.name) / "parked-save-task"
        attacker = Path(self.directory.name) / "attacker-save-task"
        attacker.mkdir()
        attacker_checkpoint = attacker / "checkpoint.json"
        attacker_checkpoint.write_bytes(b"attacker-sentinel")
        real_open = os.open
        swapped = False

        def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            name = Path(os.fspath(path)).name
            if not swapped and name.startswith(".checkpoint.") and name.endswith(".tmp"):
                task.rename(parked)
                task.symlink_to(attacker, target_is_directory=True)
                swapped = True
            kwargs = {} if dir_fd is None else {"dir_fd": dir_fd}
            return real_open(path, flags, mode, **kwargs)

        with mock.patch("scripts.checkpoint.os.open", side_effect=swapping_open):
            with self.assertRaises(CheckpointError):
                self.store.save_stage(record_id, "prepared", {"attempt": 1})

        self.assertTrue(swapped)
        self.assertEqual(b"attacker-sentinel", attacker_checkpoint.read_bytes())
        self.assertEqual(prior, (parked / "checkpoint.json").read_bytes())
        self.assertEqual(["checkpoint.json"], sorted(path.name for path in parked.iterdir()))
        self.assertEqual(["checkpoint.json"], sorted(path.name for path in attacker.iterdir()))

    @unittest.skipUnless(
        os.open in os.supports_dir_fd and os.rename in os.supports_dir_fd,
        "descriptor-relative filesystem operations required",
    )
    def test_transition_check_and_replace_use_one_task_directory_handle(self) -> None:
        record_id = "recTransitionSwap1"
        task = self.root / record_id
        self.store.save_stage(record_id, "prepared", {"origin": "original"})
        task_status = task.stat()

        alternate_root = Path(self.directory.name) / "alternate-checkpoints"
        alternate_store = CheckpointStore(alternate_root)
        alternate_store.save_stage(record_id, "prepared", {"origin": "replacement"})
        alternate_store.save_stage(record_id, "queries_resolved", {"origin": "replacement"})
        alternate_store.save_stage(record_id, "evidence_validated", evidence_payload())
        alternate_store.save_stage(record_id, "finalized", {"origin": "replacement"})
        replacement = alternate_root / record_id
        parked = Path(self.directory.name) / "parked-transition-task"
        real_close = os.close
        swapped = False

        def swapping_close(file_descriptor: int) -> None:
            nonlocal swapped
            try:
                descriptor_status = os.fstat(file_descriptor)
                is_original_task = (
                    stat.S_ISDIR(descriptor_status.st_mode)
                    and (descriptor_status.st_dev, descriptor_status.st_ino)
                    == (task_status.st_dev, task_status.st_ino)
                )
            except OSError:
                is_original_task = False
            real_close(file_descriptor)
            if is_original_task and not swapped:
                task.rename(parked)
                replacement.rename(task)
                swapped = True

        with mock.patch("scripts.checkpoint.os.close", side_effect=swapping_close):
            self.store.save_stage(record_id, "queries_resolved", {"origin": "new-save"})

        named = CheckpointStore(self.root).load(record_id)
        self.assertTrue(swapped)
        self.assertEqual("finalized", named["stage"])  # type: ignore[index]
        self.assertEqual(
            {"origin": "replacement"},
            named["stages"]["finalized"],  # type: ignore[index]
        )

    def test_open_root_closes_descriptor_when_fstat_fails(self) -> None:
        self.root.mkdir()
        real_open = os.open
        opened: list[int] = []

        def recording_open(path, flags, mode=0o777, *, dir_fd=None):
            kwargs = {} if dir_fd is None else {"dir_fd": dir_fd}
            descriptor = real_open(path, flags, mode, **kwargs)
            opened.append(descriptor)
            return descriptor

        with mock.patch("scripts.checkpoint.os.open", side_effect=recording_open):
            with mock.patch("scripts.checkpoint.os.fstat", side_effect=OSError("fixture fstat failure")):
                with self.assertRaises(CheckpointError):
                    self.store.load("recFstatClose1")

        leaked: list[int] = []
        for descriptor in opened:
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            leaked.append(descriptor)
            os.close(descriptor)
        self.assertEqual([], leaked)

    def test_task_cleanup_closes_root_even_when_task_close_raises(self) -> None:
        self.store.save_stage("recClosePath1", "prepared", {})
        real_open = os.open
        real_close = os.close
        opened: list[int] = []

        def recording_open(path, flags, mode=0o777, *, dir_fd=None):
            kwargs = {} if dir_fd is None else {"dir_fd": dir_fd}
            descriptor = real_open(path, flags, mode, **kwargs)
            opened.append(descriptor)
            return descriptor

        def closing_then_raising(file_descriptor: int) -> None:
            real_close(file_descriptor)
            if len(opened) >= 2 and file_descriptor == opened[1]:
                raise OSError("fixture task close failure")

        error: BaseException | None = None
        with mock.patch("scripts.checkpoint.os.open", side_effect=recording_open):
            with mock.patch("scripts.checkpoint.os.close", side_effect=closing_then_raising):
                with mock.patch.object(
                    self.store,
                    "_verify_task_binding",
                    side_effect=CheckpointError("fixture binding failure"),
                ):
                    try:
                        self.store.load("recClosePath1")
                    except BaseException as exc:
                        error = exc

        leaked: list[int] = []
        for descriptor in opened:
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            leaked.append(descriptor)
            real_close(descriptor)
        self.assertIsInstance(error, CheckpointError)
        self.assertEqual([], leaked)

    @unittest.skipUnless(os.name == "posix", "directory fsync behavior requires POSIX")
    def test_unsupported_directory_fsync_bad_descriptor_does_not_fail_after_replace(self) -> None:
        real_fsync = os.fsync

        def unsupported_directory_fsync(file_descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(file_descriptor).st_mode):
                raise OSError(errno.EBADF, "directory fsync unsupported")
            real_fsync(file_descriptor)

        with mock.patch("scripts.checkpoint.os.fsync", side_effect=unsupported_directory_fsync):
            path = self.store.save_stage("recFsyncPort1", "prepared", {"ok": True})

        self.assertEqual(True, json.loads(path.read_bytes())["stages"]["prepared"]["ok"])


class CheckpointSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "checkpoints"
        self.store = CheckpointStore(self.root)

    def write_raw(self, record_id: str, value: object) -> Path:
        task = self.root / record_id
        task.mkdir(parents=True, exist_ok=True)
        path = task / "checkpoint.json"
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def valid_envelope(self, record_id: str = "recValid1") -> dict[str, object]:
        return {
            "record_id": record_id,
            "stage": "prepared",
            "stages": {"prepared": {}},
            "version": 2,
        }

    def test_record_identifier_rejects_traversal_separators_and_unicode_lookalikes(self) -> None:
        invalid = (
            "", "rec", "ABC123", "../recABC", "recABC/child", "recABC\\child",
            "/recABC", "rec..", "recABC／child", "recABC∕child", "recABC⁄child",
        )
        for record_id in invalid:
            with self.subTest(record_id=record_id):
                with self.assertRaises(CheckpointError):
                    self.store.load(record_id)
                with self.assertRaises(CheckpointError):
                    self.store.save_stage(record_id, "prepared", {})

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinked_task_directory_is_rejected_for_load_and_save(self) -> None:
        self.root.mkdir()
        target = Path(self.directory.name) / "elsewhere"
        target.mkdir()
        (self.root / "recLink1").symlink_to(target, target_is_directory=True)

        with self.assertRaises(CheckpointError):
            self.store.load("recLink1")
        with self.assertRaises(CheckpointError):
            self.store.save_stage("recLink1", "prepared", {})

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlinked_checkpoint_file_is_rejected_for_load_and_save(self) -> None:
        target = Path(self.directory.name) / "elsewhere.json"
        target.write_text("{}", encoding="utf-8")
        task = self.root / "recLink2"
        task.mkdir(parents=True)
        (task / "checkpoint.json").symlink_to(target)

        with self.assertRaises(CheckpointError):
            self.store.load("recLink2")
        with self.assertRaises(CheckpointError):
            self.store.save_stage("recLink2", "prepared", {})

    def test_malformed_truncated_and_non_utf8_files_raise_bounded_error(self) -> None:
        hostile = b'{"record_id":"recBad1","private":"DO-NOT-ECHO"'
        for index, raw in enumerate((hostile, b"\xff\xfe", b"[]"), 1):
            record_id = f"recBad{index}"
            self.write_raw(record_id, raw)
            with self.subTest(raw=raw):
                with self.assertRaises(CheckpointError) as raised:
                    self.store.load(record_id)
                self.assertLessEqual(len(str(raised.exception)), 160)
                self.assertNotIn("DO-NOT-ECHO", str(raised.exception))

    def test_envelope_version_record_stage_and_exact_stage_keys_are_validated(self) -> None:
        cases: list[dict[str, object]] = []
        for changes in (
            {"version": 3},
            {"version": True},
            {"record_id": "recOther1"},
            {"stage": "unknown"},
            {"stages": {}},
            {"stages": {"prepared": {}, "finalized": {}}},
            {"extra": False},
        ):
            envelope = self.valid_envelope("recEnvelope1")
            envelope.update(changes)
            cases.append(envelope)
        missing = self.valid_envelope("recEnvelope1")
        del missing["stage"]
        cases.append(missing)

        for index, envelope in enumerate(cases):
            self.write_raw("recEnvelope1", envelope)
            with self.subTest(index=index, envelope=envelope):
                with self.assertRaises(CheckpointError):
                    self.store.load("recEnvelope1")

    def test_skipped_and_regressed_transitions_are_rejected_without_changing_state(self) -> None:
        with self.assertRaises(CheckpointError):
            self.store.save_stage("recTransition1", "evidence_validated", {})
        self.store.save_stage("recTransition1", "prepared", {"step": 1})
        with self.assertRaises(CheckpointError):
            self.store.save_stage("recTransition1", "finalized", {})
        self.store.save_stage("recTransition1", "queries_resolved", {"step": 2})
        self.store.save_stage("recTransition1", "evidence_validated", evidence_payload())
        with self.assertRaises(CheckpointError):
            self.store.save_stage("recTransition1", "prepared", {})

        loaded = self.store.load("recTransition1")
        self.assertEqual("evidence_validated", loaded["stage"])  # type: ignore[index]
        self.assertEqual({"step": 1}, loaded["stages"]["prepared"])  # type: ignore[index]

    def test_payload_rejects_non_json_values_non_string_keys_cycles_and_nonfinite_numbers(self) -> None:
        cyclic_list: list[object] = []
        cyclic_list.append(cyclic_list)
        cyclic_mapping: dict[str, object] = {}
        cyclic_mapping["self"] = cyclic_mapping
        invalid: tuple[object, ...] = (
            {"bad": {1, 2}},
            {"bad": b"bytes"},
            {"bad": Path("relative")},
            {"bad": object()},
            {1: "non-string key"},
            {"bad": cyclic_list},
            cyclic_mapping,
            {"bad": float("nan")},
            {"bad": float("inf")},
            {"bad": float("-inf")},
        )
        for index, payload in enumerate(invalid):
            with self.subTest(index=index, value_type=type(payload).__name__):
                with self.assertRaises(CheckpointError):
                    self.store.save_stage(f"recType{index}", "prepared", payload)  # type: ignore[arg-type]

    def test_all_forbidden_key_categories_are_rejected_with_separator_normalization(self) -> None:
        forbidden_keys = (
            "API-KEY", "apikey", "x authorization value", "PASSWORD", "client.secret",
            "browser-cookie", "user/session/id", "ACCESS TOKEN", "refresh-token",
            "base.token", "PRIVATE KEY material",
        )
        for index, key in enumerate(forbidden_keys):
            with self.subTest(key=key):
                with self.assertRaises(CheckpointError) as raised:
                    self.store.save_stage(f"recKey{index}", "prepared", {"nested": {key: "redacted"}})
                message = str(raised.exception)
                self.assertIn("key", message.lower())
                self.assertNotIn("redacted", message)

    def test_forbidden_key_text_is_absent_from_exception_and_formatted_traceback(self) -> None:
        rejected_key = "password-hunter2"
        payload = {"nested": {rejected_key: "ordinary-value"}}

        try:
            self.store.save_stage("recKeyLeak1", "prepared", payload)
        except CheckpointError as exc:
            rendered = "".join(traceback.format_exception(exc))
        else:
            self.fail("forbidden key was accepted")

        self.assertNotIn(rejected_key, rendered)

    def test_only_exact_approved_identifier_key_spellings_are_exempt(self) -> None:
        for index, key in enumerate(("FILE TOKEN", "file-token", "image.token")):
            with self.subTest(key=key):
                with self.assertRaises(CheckpointError):
                    self.store.save_stage(f"recIdentifier{index}", "prepared", {key: "ordinary-value"})

    def test_standalone_cookie_assignment_is_rejected(self) -> None:
        with self.assertRaises(CheckpointError) as raised:
            self.store.save_stage("recCookie1", "prepared", {"note": "sid=abc"})

        self.assertNotIn("sid=abc", str(raised.exception))

    def test_all_forbidden_string_forms_are_rejected(self) -> None:
        forbidden_strings = (
            "uses ARK_API_KEY from environment",
            "Authorization: Basic abc",
            "prefix Bearer abc.def suffix",
            "Cookie: sid=abc",
            "sid=abc; theme=dark",
            "api-key = abc",
            "token: abc",
            "secret=abc",
            "data:image/png;base64,QUJDREVGRw==",
        )
        for index, value in enumerate(forbidden_strings):
            with self.subTest(value=value):
                with self.assertRaises(CheckpointError) as raised:
                    self.store.save_stage(f"recString{index}", "prepared", {"note": value})
                self.assertNotIn(value, str(raised.exception))

    def test_configured_forbidden_value_is_rejected_as_a_substring_on_save_and_load(self) -> None:
        fixture_secret = "fixture-secret-8675309"
        store = CheckpointStore(self.root, forbidden_values=[fixture_secret])
        with self.assertRaises(CheckpointError):
            store.save_stage("recSecret1", "prepared", {"note": f"prefix-{fixture_secret}-suffix"})

        envelope = self.valid_envelope("recSecret2")
        envelope["stages"] = {"prepared": {"note": f"prefix-{fixture_secret}-suffix"}}
        self.write_raw("recSecret2", envelope)
        with self.assertRaises(CheckpointError):
            store.load("recSecret2")

    def test_configured_secret_in_a_key_is_not_echoed_and_bytes_are_not_values(self) -> None:
        fixture_secret = "fixture-secret-key-271828"
        store = CheckpointStore(self.root, forbidden_values=[fixture_secret])

        with self.assertRaises(CheckpointError) as raised:
            store.save_stage("recSecretKey1", "prepared", {fixture_secret: "value"})

        self.assertNotIn(fixture_secret, str(raised.exception))
        with self.assertRaises(TypeError):
            CheckpointStore(self.root, forbidden_values=b"not-a-string-iterable")

    def test_approved_identifier_keys_and_ordinary_values_are_allowed(self) -> None:
        payload = {
            "file_token": "file-123",
            "image_token": "image-123",
            "record_id": "record-123",
            "product_id": "product-123",
            "table_id": "table-123",
            "view_id": "view-123",
        }
        self.store.save_stage("recAllowed1", "prepared", payload)
        self.assertEqual(payload, self.store.load("recAllowed1")["stages"]["prepared"])  # type: ignore[index]

    def test_load_rejects_secret_keys_and_strings_in_an_otherwise_valid_envelope(self) -> None:
        for index, payload in enumerate((
            {"api_key": "redacted"},
            {"note": "Authorization: Basic redacted"},
            {"note": "data:image/jpeg;base64,UkVEQUNURUQ="},
        )):
            record_id = f"recLoadSecret{index}"
            envelope = self.valid_envelope(record_id)
            envelope["stages"] = {"prepared": payload}
            self.write_raw(record_id, envelope)
            with self.subTest(index=index):
                with self.assertRaises(CheckpointError) as raised:
                    self.store.load(record_id)
                self.assertNotIn("redacted", str(raised.exception).lower())

    def test_secret_never_appears_in_exception_or_formatted_traceback(self) -> None:
        fixture_secret = "fixture-never-emit-314159"
        store = CheckpointStore(self.root, forbidden_values=[fixture_secret])
        payload = {"note": "prefix-" + fixture_secret + "-suffix"}

        try:
            store.save_stage("recTrace1", "prepared", payload)
        except CheckpointError as exc:
            rendered = "".join(traceback.format_exception(exc))
            message = str(exc)
        else:
            self.fail("secret-bearing payload was accepted")

        self.assertNotIn(fixture_secret, rendered)
        self.assertLessEqual(len(message), 200)

    def test_completed_validates_requested_stage_before_loading(self) -> None:
        with mock.patch.object(self.store, "load", side_effect=AssertionError("must not load")):
            with self.assertRaises(CheckpointError):
                self.store.completed("recStage1", "unknown")
        self.assertEqual(("prepared", "queries_resolved", "evidence_validated", "finalized"), STAGES)


if __name__ == "__main__":
    unittest.main()
