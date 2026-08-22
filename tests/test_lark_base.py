import json
import os
import stat
import subprocess
import tempfile
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.lark_base import (
    RESULT_BASE_TOKEN,
    RESULT_FIELDS,
    RESULT_TABLE_ID,
    TASK_BASE_TOKEN,
    TASK_FIELDS,
    TASK_TABLE_ID,
    LarkBaseClient,
    LarkBaseError,
    ResultWrite,
)
from scripts.models import Task, VerifiedCandidate
from scripts.platforms import Platform


def cli_result(payload, *, returncode=0, stderr=""):
    stdout = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def write_download_output(args, kwargs, content):
    output_name = args[args.index("--output") + 1]
    inherited = kwargs.get("pass_fds")
    if inherited:
        fd = os.open(output_name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600, dir_fd=inherited[0])
        try:
            os.write(fd, content)
        finally:
            os.close(fd)
    else:
        (Path(kwargs["cwd"]) / output_name).write_bytes(content)


def envelope(*, fields, types, rows=(), record_ids=(), has_more=False, extra=None):
    data = {
        "data": list(rows),
        "fields": list(fields),
        "field_type_list": list(types),
        "record_id_list": list(record_ids),
        "has_more": has_more,
    }
    if extra:
        data.update(extra)
    return {"ok": True, "data": data}


def field_envelope(field_map, *, total=None, extra=None):
    fields = [
        {"id": f"fld-{index}", "name": name, "type": field_type}
        for index, (name, field_type) in enumerate(field_map.items())
    ]
    data = {"fields": fields, "total": len(fields) if total is None else total}
    if extra:
        data.update(extra)
    return {"ok": True, "data": data}


TASK_FIELD_ORDER = list(TASK_FIELDS)
TASK_TYPE_ORDER = [TASK_FIELDS[name] for name in TASK_FIELD_ORDER]
RESULT_FIELD_ORDER = list(RESULT_FIELDS)
RESULT_TYPE_ORDER = [RESULT_FIELDS[name] for name in RESULT_FIELD_ORDER]


def task_values(**changes):
    values = {
        "SKU": "SKU-9",
        "原图": [{"file_token": "file-token-9", "name": "look.jpg"}],
        "评分": "4.6 de 5",
        "评价数": "1,200",
        "结果数量": "15",
        "vendidos 数": "1.2k sold",
        "平台": "[https://us.shein.com/](https://us.shein.com/)",
        "任务状态": ["未开始"],
    }
    values.update(changes)
    return [values[name] for name in TASK_FIELD_ORDER]


class FakeRunner:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), dict(kwargs)))
        if not self.outcomes:
            raise AssertionError(f"unexpected command label: {args[2]}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(list(args), dict(kwargs))
        return outcome


class LarkReadTests(unittest.TestCase):
    def test_validate_contracts_uses_exact_safe_commands_for_both_bases(self):
        runner = FakeRunner([
            cli_result(field_envelope(TASK_FIELDS)),
            cli_result(field_envelope(RESULT_FIELDS)),
        ])

        LarkBaseClient(runner=runner).validate_base_contracts()

        self.assertEqual([
            [
                "lark-cli", "base", "+field-list", "--base-token", TASK_BASE_TOKEN,
                "--table-id", TASK_TABLE_ID, "--format", "json", "--as", "user",
            ],
            [
                "lark-cli", "base", "+field-list", "--base-token", RESULT_BASE_TOKEN,
                "--table-id", RESULT_TABLE_ID, "--format", "json", "--as", "user",
            ],
        ], [call[0] for call in runner.calls])
        for _, kwargs in runner.calls:
            self.assertEqual({
                "shell": False,
                "check": False,
                "text": True,
                "capture_output": True,
                "timeout": 30.0,
            }, kwargs)

    def test_configurable_timeout_is_applied_to_every_subprocess(self):
        runner = FakeRunner([cli_result(field_envelope(TASK_FIELDS))])
        client = LarkBaseClient(runner=runner, timeout=2.5)
        client._read_field_list("task field-list", ["+field-list"])
        self.assertEqual(2.5, runner.calls[0][1]["timeout"])
        for invalid in (True, 0, -1, float("inf"), float("nan")):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    LarkBaseClient(runner=FakeRunner([]), timeout=invalid)

    def test_validate_contracts_accumulates_missing_and_type_mismatch_errors(self):
        bad_task = dict(TASK_FIELDS)
        del bad_task["SKU"]
        bad_task["评分"] = "number"
        bad_result = dict(RESULT_FIELDS)
        del bad_result["标题"]
        bad_result["原图"] = "text"
        runner = FakeRunner([
            cli_result(field_envelope(bad_task)),
            cli_result(field_envelope(bad_result)),
        ])

        with self.assertRaises(LarkBaseError) as raised:
            LarkBaseClient(runner=runner).validate_base_contracts()

        message = str(raised.exception)
        for detail in ("SKU", "评分", "标题", "原图"):
            self.assertIn(detail, message)
        self.assertLessEqual(len(message), 500)

    def test_validate_contracts_rejects_malformed_real_field_list_objects(self):
        malformed_data = [
            {"fields": "not-a-list", "total": 0},
            {"fields": ["SKU"], "total": 1},
            {"fields": [{"name": "SKU", "type": "text"}], "total": 1},
            {"fields": [{"id": "", "name": "SKU", "type": "text"}], "total": 1},
            {"fields": [{"id": "fld-1", "type": "text"}], "total": 1},
            {"fields": [{"id": "fld-1", "name": "", "type": "text"}], "total": 1},
            {"fields": [{"id": "fld-1", "name": "SKU", "type": ""}], "total": 1},
            {"fields": [{"id": "fld-1", "name": "SKU", "type": ["text"]}], "total": 1},
            {"fields": [{"id": " fld-1", "name": "SKU", "type": "text"}], "total": 1},
            {"fields": [{"id": "fld-1", "name": " SKU", "type": "text"}], "total": 1},
            {"fields": [{"id": "fld-1", "name": "SKU", "type": "text "}], "total": 1},
        ]
        for data in malformed_data:
            with self.subTest(data=data):
                runner = FakeRunner([cli_result({"ok": True, "data": data})])
                with self.assertRaises(LarkBaseError):
                    LarkBaseClient(runner=runner).validate_base_contracts()

    def test_validate_contracts_rejects_duplicate_names_and_inconsistent_total(self):
        duplicates = [
            {
                "fields": [
                    {"id": "fld-1", "name": "SKU", "type": "text"},
                    {"id": "fld-2", "name": "SKU", "type": "text"},
                ],
                "total": 2,
            },
            {
                "fields": [
                    {"id": "fld-1", "name": "SKU", "type": "text"},
                    {"id": "fld-1", "name": "原图", "type": "attachment"},
                ],
                "total": 2,
            },
        ]
        inconsistent_totals = (-1, True, "2", 1, 3)
        for data in [*duplicates, {"fields": []}, *(
            {
                "fields": [{"id": "fld-1", "name": "SKU", "type": "text"},
                           {"id": "fld-2", "name": "原图", "type": "attachment"}],
                "total": total,
            }
            for total in inconsistent_totals
        )]:
            with self.subTest(data=data):
                runner = FakeRunner([cli_result({"ok": True, "data": data})])
                with self.assertRaises(LarkBaseError):
                    LarkBaseClient(runner=runner).validate_base_contracts()

    def test_validate_contracts_rejects_ambiguous_matrix_hybrid_without_weakening_records(self):
        hybrid = field_envelope(
            TASK_FIELDS,
            extra={"data": [], "field_type_list": [], "record_id_list": []},
        )
        with self.assertRaises(LarkBaseError):
            LarkBaseClient(runner=FakeRunner([cli_result(hybrid)])).validate_base_contracts()

        matrix = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=[task_values()],
            record_ids=["rec-still-strict"],
        )
        tasks, failures = LarkBaseClient(
            runner=FakeRunner([cli_result(matrix)])
        ).list_pending_tasks()
        self.assertEqual(["rec-still-strict"], [task.record_id for task in tasks])
        self.assertEqual([], failures)

    def test_field_list_errors_redact_hostile_type_values(self):
        payload = {
            "ok": True,
            "data": {
                "fields": [{
                    "id": "fld-1",
                    "name": "SKU",
                    "type": "Authorization: Bearer field-list-secret",
                }],
                "total": 1,
            },
        }
        with self.assertRaises(LarkBaseError) as raised:
            LarkBaseClient(runner=FakeRunner([cli_result(payload)])).validate_base_contracts()
        self.assertNotIn("field-list-secret", str(raised.exception))

    def test_rejects_transport_and_malformed_cli_envelopes_with_bounded_redaction(self):
        cases = [
            cli_result("not-json"),
            cli_result({"ok": False, "data": {}, "token": "should-not-leak"}),
            cli_result({"ok": True, "data": []}),
            cli_result({"ok": True}),
            cli_result({"ok": True, "data": {}}, returncode=2, stderr="Authorization: Bearer private-value"),
        ]
        for result in cases:
            with self.subTest(stdout=result.stdout, returncode=result.returncode):
                runner = FakeRunner([result])
                with self.assertRaises(LarkBaseError) as raised:
                    LarkBaseClient(runner=runner).list_pending_tasks()
                message = str(raised.exception)
                self.assertLessEqual(len(message), 500)
                self.assertNotIn("private-value", message)
                self.assertNotIn("should-not-leak", message)

    def test_runner_timeout_never_exposes_argv_json_or_exception_output(self):
        hostile_token = "hostile-base-token"
        hostile_payload = '{"secret":"hostile-json-value"}'
        timeout = subprocess.TimeoutExpired(
            cmd=[
                "lark-cli", "base", "+record-list", "--base-token", hostile_token,
                "--json", hostile_payload,
            ],
            timeout=3,
            output="hostile-stdout-value",
            stderr="Authorization: Bearer hostile-stderr-value",
        )

        with self.assertRaises(LarkBaseError) as raised:
            LarkBaseClient(runner=FakeRunner([timeout])).list_pending_tasks()

        message = str(raised.exception)
        self.assertIn("task record-list", message)
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        for secret in (
            hostile_token,
            hostile_payload,
            "hostile-json-value",
            "hostile-stdout-value",
            "hostile-stderr-value",
            "Authorization",
        ):
            self.assertNotIn(secret, message)

    def test_rejects_misaligned_matrix_and_has_more_page_stall(self):
        misaligned = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER[:-1],
            rows=[task_values()],
            record_ids=["rec-1"],
        )
        stalled = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=[],
            record_ids=[],
            has_more=True,
        )
        for payload in (misaligned, stalled):
            with self.subTest(payload=payload):
                with self.assertRaises(LarkBaseError):
                    LarkBaseClient(runner=FakeRunner([cli_result(payload)])).list_pending_tasks()

    def test_rejects_duplicate_matrix_fields_and_numeric_threshold_cells(self):
        duplicate_fields = list(TASK_FIELD_ORDER)
        duplicate_fields[-1] = duplicate_fields[0]
        duplicate = envelope(
            fields=duplicate_fields,
            types=TASK_TYPE_ORDER,
            rows=[task_values()],
            record_ids=["rec-duplicate-field"],
        )
        with self.assertRaises(LarkBaseError):
            LarkBaseClient(runner=FakeRunner([cli_result(duplicate)])).list_pending_tasks()

        numeric = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=[task_values(**{"vendidos 数": 0, "评价数": 0, "评分": 4.5})],
            record_ids=["rec-numeric-thresholds"],
        )
        tasks, failures = LarkBaseClient(
            runner=FakeRunner([cli_result(numeric)])
        ).list_pending_tasks()
        self.assertEqual([], tasks)
        self.assertEqual(["rec-numeric-thresholds"], [item.record_id for item in failures])

    def test_pages_by_actual_row_count_and_returns_valid_tasks_while_isolating_bad_rows(self):
        first = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=[task_values(), task_values(SKU=" ", **{"评分": "4.8"})],
            record_ids=["rec-good", "rec-bad-sku"],
            has_more=True,
        )
        second = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=[
                task_values(**{"任务状态": ["成功"]}),
                task_values(**{"评价数": "about 20", "结果数量": "0"}),
            ],
            record_ids=["rec-done", "rec-bad-metrics"],
        )
        runner = FakeRunner([cli_result(first), cli_result(second)])

        tasks, failures = LarkBaseClient(runner=runner).list_pending_tasks()

        self.assertEqual(1, len(tasks))
        task = tasks[0]
        self.assertEqual("rec-good", task.record_id)
        self.assertEqual("SKU-9", task.sku)
        self.assertEqual("file-token-9", task.image_token)
        self.assertEqual("look.jpg", task.image_name)
        self.assertIs(Platform.SHEIN_US, task.platform)
        self.assertEqual((1200, 1200, 4.6, 15), (
            task.min_sold, task.min_reviews, task.min_rating, task.result_limit,
        ))
        self.assertEqual(["rec-bad-sku", "rec-bad-metrics"], [item.record_id for item in failures])
        self.assertTrue(all(len(item.message) <= 500 for item in failures))
        self.assertEqual(["0", "2"], [
            call[0][call[0].index("--offset") + 1] for call in runner.calls
        ])
        self.assertEqual(["200", "200"], [
            call[0][call[0].index("--limit") + 1] for call in runner.calls
        ])

    def test_record_scope_uses_get_and_reports_missing_or_nonpending_record(self):
        nonpending = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=[task_values(**{"任务状态": ["失败"]})],
            record_ids=["rec-7"],
        )
        missing = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=[],
            record_ids=[],
        )
        for payload, record_id in ((nonpending, "rec-7"), (missing, "rec-missing")):
            with self.subTest(record_id=record_id):
                runner = FakeRunner([cli_result(payload)])
                tasks, failures = LarkBaseClient(runner=runner).list_pending_tasks(record_id)
                self.assertEqual([], tasks)
                self.assertEqual(1, len(failures))
                self.assertEqual(record_id, failures[0].record_id)
                self.assertIn("record scope", failures[0].message)
                self.assertEqual([
                    "lark-cli", "base", "+record-get", "--base-token", TASK_BASE_TOKEN,
                    "--table-id", TASK_TABLE_ID, "--record-id", record_id,
                    "--format", "json", "--as", "user",
                ], runner.calls[0][0])

    def test_record_scope_rejects_a_single_row_with_a_different_record_id(self):
        mismatched = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=[task_values()],
            record_ids=["rec-other"],
        )

        tasks, failures = LarkBaseClient(
            runner=FakeRunner([cli_result(mismatched)])
        ).list_pending_tasks("rec-requested")

        self.assertEqual([], tasks)
        self.assertEqual(1, len(failures))
        self.assertEqual("rec-requested", failures[0].record_id)
        self.assertIn("record scope", failures[0].message)

    def test_get_task_state_parses_exact_success_row_without_treating_it_as_pending(self):
        payload = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=[task_values(**{"任务状态": ["成功"]})],
            record_ids=["rec-success"],
        )
        task_value, status = LarkBaseClient(
            runner=FakeRunner([cli_result(payload)])
        ).get_task_state("rec-success")
        self.assertEqual("rec-success", task_value.record_id)
        self.assertEqual("成功", status)

    def test_task_parser_requires_first_complete_attachment_and_unambiguous_thresholds(self):
        invalid_rows = [
            task_values(**{"原图": []}),
            task_values(**{"原图": [{"file_token": "", "name": "x.jpg"}]}),
            task_values(**{"原图": [{"file_token": "a", "name": ""}]}),
            task_values(**{"vendidos 数": "1.2"}),
            task_values(**{"评分": "5.1"}),
            task_values(**{"结果数量": "2.0"}),
            task_values(**{"平台": "https://example.com"}),
        ]
        payload = envelope(
            fields=TASK_FIELD_ORDER,
            types=TASK_TYPE_ORDER,
            rows=invalid_rows,
            record_ids=[f"bad-{index}" for index in range(len(invalid_rows))],
        )

        tasks, failures = LarkBaseClient(runner=FakeRunner([cli_result(payload)])).list_pending_tasks()

        self.assertEqual([], tasks)
        self.assertEqual(len(invalid_rows), len(failures))


def sample_task(*, image_name="look.jpg", platform=Platform.SHEIN_US):
    return Task(
        record_id="task-1",
        sku="SKU-9",
        image_token="file-token-9",
        image_name=image_name,
        platform=platform,
        min_sold=100,
        min_reviews=20,
        min_rating=4.2,
        result_limit=3,
    )


def sample_candidate(*, platform=Platform.SHEIN_US, canonical_url=None):
    if canonical_url is None:
        canonical_url = "https://us.shein.com/black-dress-p-123.html?utm_source=ad"
    return VerifiedCandidate(
        identity="shein-us:123" if platform is Platform.SHEIN_US else "mercado-libre-mx:MLM123",
        platform=platform,
        title="Black fitted dress",
        canonical_url=canonical_url,
        query_hits=frozenset({"black dress", "party dress"}),
        earliest_organic_rank=2,
        earliest_ad_rank=None,
        sold_display="1.2k sold",
        sold_value=1200,
        reviews_display="84",
        reviews_value=84,
        rating_display="4.7 de 5",
        rating_value=4.7,
        match_level="高度相似",
        visual_features=("方领", "长袖"),
    )


def result_values(*, attachment=None, title="Black fitted dress", url=None):
    if url is None:
        url = "https://us.shein.com/black-dress-p-123.html"
    values = {
        "SKU": "SKU-9",
        "原图": [] if attachment is None else attachment,
        "平台": "SHEIN US",
        "标题": title,
        "爆款链接": url,
        "vendidos 数": "1.2k sold",
        "评价数": "84",
        "评分": "4.7 de 5",
        "视觉特征": "高度相似｜方领；长袖",
    }
    return [values[name] for name in RESULT_FIELD_ORDER]


def result_matrix(rows=(), record_ids=(), *, has_more=False):
    return envelope(
        fields=RESULT_FIELD_ORDER,
        types=RESULT_TYPE_ORDER,
        rows=rows,
        record_ids=record_ids,
        has_more=has_more,
    )


class LarkWriteTests(unittest.TestCase):
    def test_verify_existing_result_is_read_only_and_requires_fields_and_content(self):
        client = LarkBaseClient(runner=FakeRunner([]))
        task_value = sample_task()
        candidate = sample_candidate()
        payload = client._result_payload(task_value, candidate)
        existing = {"_record_id": "rec-result", **payload, "原图": [{"file_token": "tok", "name": "look.jpg"}]}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "look.jpg"
            source.write_bytes(b"source")
            with mock.patch.object(client, "find_existing_result", return_value=existing), mock.patch.object(
                client, "_file_sha256", return_value="digest"
            ), mock.patch.object(
                client, "_has_matching_attachment_content", return_value=True
            ) as content_check:
                self.assertTrue(client.verify_existing_result(task_value, candidate, source))
        content_check.assert_called_once()
        self.assertEqual([], client._runner.calls)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source.jpg"
        self.source.write_bytes(b"image-data")

    def test_find_existing_result_pages_and_matches_the_exact_canonical_business_key(self):
        first = result_matrix(
            rows=[result_values(url="not a URL")],
            record_ids=["bad-url"],
            has_more=True,
        )
        second = result_matrix(
            rows=[result_values(url="https://us.shein.com/black-dress-p-123.html?utm_source=old")],
            record_ids=["result-7"],
        )
        runner = FakeRunner([cli_result(first), cli_result(second)])

        found = LarkBaseClient(runner=runner).find_existing_result(
            "SKU-9",
            Platform.SHEIN_US,
            "https://us.shein.com/black-dress-p-123.html?fbclid=new",
        )

        self.assertIsNotNone(found)
        self.assertEqual("result-7", found["_record_id"])
        self.assertEqual(["0", "1"], [
            call[0][call[0].index("--offset") + 1] for call in runner.calls
        ])

    def test_duplicate_business_key_rows_are_rejected_instead_of_updating_arbitrarily(self):
        duplicate_rows = result_matrix(
            rows=[result_values(), result_values()],
            record_ids=["result-1", "result-2"],
        )
        runner = FakeRunner([cli_result(duplicate_rows)])

        with self.assertRaises(LarkBaseError) as raised:
            LarkBaseClient(runner=runner).find_existing_result(
                "SKU-9", Platform.SHEIN_US,
                "https://us.shein.com/black-dress-p-123.html",
            )

        self.assertIn("duplicate", str(raised.exception).lower())

    def test_create_writes_eight_deterministic_text_fields_uploads_attachment_then_verifies_nine_fields(self):
        canonical = "https://us.shein.com/black-dress-p-123.html"
        current = result_matrix(
            rows=[result_values()],
            record_ids=["result-new"],
        )
        verified = result_matrix(
            rows=[result_values(attachment=[{"name": "look.jpg", "file_token": "stored"}])],
            record_ids=["result-new"],
        )
        runner = FakeRunner([
            cli_result(result_matrix()),
            cli_result({"ok": True, "data": {"record_id": "result-new"}}),
            cli_result(current),
            cli_result({"ok": True, "data": {}}),
            cli_result(verified),
            lambda args, kwargs: (
                write_download_output(args, kwargs, b"image-data")
                or cli_result({"ok": True, "data": {}})
            ),
        ])

        result = LarkBaseClient(runner=runner).write_result(
            sample_task(), sample_candidate(), self.source,
        )

        self.assertEqual(ResultWrite(
            record_id="result-new",
            created=True,
            attachment_uploaded=True,
            verified_fields=tuple(sorted(RESULT_FIELDS)),
        ), result)
        upsert = runner.calls[1][0]
        self.assertNotIn("--record-id", upsert)
        self.assertEqual([
            "lark-cli", "base", "+record-upsert", "--base-token", RESULT_BASE_TOKEN,
            "--table-id", RESULT_TABLE_ID,
        ], upsert[:7])
        payload = upsert[upsert.index("--json") + 1]
        expected_payload = {
            "SKU": "SKU-9",
            "平台": "SHEIN US",
            "标题": "Black fitted dress",
            "爆款链接": canonical,
            "vendidos 数": "1.2k sold",
            "评价数": "84",
            "评分": "4.7 de 5",
            "视觉特征": "高度相似｜方领；长袖",
        }
        self.assertEqual(
            json.dumps(expected_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            payload,
        )
        self.assertNotIn("原图", json.loads(payload))
        upload = runner.calls[3][0]
        self.assertEqual([
            "lark-cli", "base", "+record-upload-attachment",
            "--base-token", RESULT_BASE_TOKEN, "--table-id", RESULT_TABLE_ID,
            "--record-id", "result-new", "--field-id", "原图",
            "--file", str(self.source), "--format", "json", "--as", "user",
        ], upload)

    def test_update_skips_upload_only_for_verified_matching_attachment_content(self):
        existing_row = result_values(
            attachment=[{"name": "look.jpg", "file_token": "already"}],
        )
        runner = FakeRunner([
            cli_result(result_matrix(rows=[existing_row], record_ids=["result-2"])),
            cli_result({"ok": True, "data": {"record_id": "result-2"}}),
            lambda args, kwargs: (
                write_download_output(args, kwargs, b"image-data")
                or cli_result({"ok": True, "data": {}})
            ),
            cli_result(result_matrix(rows=[existing_row], record_ids=["result-2"])),
            lambda args, kwargs: (
                write_download_output(args, kwargs, b"image-data")
                or cli_result({"ok": True, "data": {}})
            ),
        ])

        result = LarkBaseClient(runner=runner).write_result(
            sample_task(), sample_candidate(), self.source,
        )

        self.assertFalse(result.created)
        self.assertFalse(result.attachment_uploaded)
        upsert = runner.calls[1][0]
        self.assertEqual("result-2", upsert[upsert.index("--record-id") + 1])
        self.assertFalse(any("+record-upload-attachment" in args for args, _ in runner.calls))

    def test_readback_rehashes_even_when_attachment_token_is_unchanged(self):
        existing_row = result_values(
            attachment=[{"name": "look.jpg", "file_token": "same-token"}],
        )
        downloads = iter((b"image-data", b"changed-after-lookup"))

        def changing_download(args, kwargs):
            write_download_output(args, kwargs, next(downloads))
            return cli_result({"ok": True, "data": {}})

        runner = FakeRunner([
            cli_result(result_matrix(rows=[existing_row], record_ids=["result-2"])),
            cli_result({"ok": True, "data": {"record_id": "result-2"}}),
            changing_download,
            cli_result(result_matrix(rows=[existing_row], record_ids=["result-2"])),
            changing_download,
        ])

        with self.assertRaisesRegex(LarkBaseError, "result-2"):
            LarkBaseClient(runner=runner).write_result(
                sample_task(), sample_candidate(), self.source,
            )

    def test_same_name_wrong_content_uploads_and_verifies_replacement_content(self):
        existing_row = result_values(
            attachment=[{"name": "look.jpg", "file_token": "wrong-token"}],
        )
        verified_row = result_values(
            attachment=[{"name": "look.jpg", "file_token": "right-token"}],
        )

        def download(args, kwargs):
            token = args[args.index("--file-token") + 1]
            write_download_output(
                args, kwargs, b"wrong-content" if token == "wrong-token" else b"image-data"
            )
            return cli_result({"ok": True, "data": {}})

        runner = FakeRunner([
            cli_result(result_matrix(rows=[existing_row], record_ids=["result-2"])),
            cli_result({"ok": True, "data": {"record_id": "result-2"}}),
            download,
            cli_result({"ok": True, "data": {}}),
            cli_result(result_matrix(rows=[verified_row], record_ids=["result-2"])),
            download,
        ])

        result = LarkBaseClient(runner=runner).write_result(
            sample_task(), sample_candidate(), self.source,
        )

        self.assertTrue(result.attachment_uploaded)
        self.assertEqual(1, sum("+record-upload-attachment" in args for args, _ in runner.calls))

    def test_uploaded_attachment_with_wrong_content_fails_readback(self):
        verified_row = result_values(
            attachment=[{"name": "look.jpg", "file_token": "wrong-token"}],
        )

        def download_wrong(args, kwargs):
            write_download_output(args, kwargs, b"wrong-content")
            return cli_result({"ok": True, "data": {}})

        runner = FakeRunner([
            cli_result(result_matrix()),
            cli_result({"ok": True, "data": {"record_id": "result-new"}}),
            cli_result(result_matrix(rows=[result_values()], record_ids=["result-new"])),
            cli_result({"ok": True, "data": {}}),
            cli_result(result_matrix(rows=[verified_row], record_ids=["result-new"])),
            download_wrong,
        ])
        with self.assertRaisesRegex(LarkBaseError, "result-new"):
            LarkBaseClient(runner=runner).write_result(
                sample_task(), sample_candidate(), self.source,
            )

    def test_readback_mismatch_after_partial_create_names_record_id_for_resume(self):
        current = result_matrix(
            rows=[result_values()],
            record_ids=["result-partial"],
        )
        mismatch = result_matrix(
            rows=[result_values(
                attachment=[{"name": "source.jpg", "file_token": "stored"}],
                title="Wrong title",
            )],
            record_ids=["result-partial"],
        )
        runner = FakeRunner([
            cli_result(result_matrix()),
            cli_result({"ok": True, "data": {"record_id": "result-partial"}}),
            cli_result(current),
            cli_result({"ok": True, "data": {}}),
            cli_result(mismatch),
        ])

        with self.assertRaises(LarkBaseError) as raised:
            LarkBaseClient(runner=runner).write_result(
                sample_task(), sample_candidate(), self.source,
            )

        self.assertIn("result-partial", str(raised.exception))

    def test_post_create_stat_exception_is_wrapped_with_record_id_without_raw_detail(self):
        class HostileStatPath(type(Path())):
            def exists(self):
                return True

            def is_symlink(self):
                return False

            def is_file(self):
                return True

            def stat(self, *args, **kwargs):
                raise OSError("raw-private-path-and-detail")

        current = result_matrix(
            rows=[result_values()],
            record_ids=["result-known"],
        )
        runner = FakeRunner([
            cli_result(result_matrix()),
            cli_result({"ok": True, "data": {"record_id": "result-known"}}),
            cli_result(current),
        ])
        hostile_path = HostileStatPath("/private/raw-source-name.jpg")

        try:
            LarkBaseClient(runner=runner).write_result(
                sample_task(), sample_candidate(), hostile_path,
            )
        except LarkBaseError as exc:
            raised = exc
        else:
            self.fail("hostile stat exception must be wrapped")

        message = str(raised)
        self.assertIn("result-known", message)
        self.assertIsNone(raised.__cause__)
        self.assertTrue(raised.__suppress_context__)
        formatted = "".join(traceback.format_exception(type(raised), raised, raised.__traceback__))
        self.assertIn("result-known", formatted)
        self.assertNotIn("raw-private-path-and-detail", message)
        self.assertNotIn("raw-source-name", message)
        self.assertNotIn("raw-private-path-and-detail", formatted)
        self.assertNotIn("raw-source-name", formatted)

    def test_dry_run_performs_lookup_but_no_upsert_upload_or_status_write(self):
        runner = FakeRunner([cli_result(result_matrix())])
        client = LarkBaseClient(runner=runner)

        result = client.write_result(
            sample_task(), sample_candidate(), self.source, dry_run=True,
        )
        client.set_task_status("task-1", "成功", dry_run=True)

        self.assertEqual(ResultWrite("dry-run", False, False, ()), result)
        self.assertEqual(["+record-list"], [call[0][2] for call in runner.calls])

    def test_status_accepts_only_success_or_failure_and_serializes_select_value(self):
        runner = FakeRunner([
            cli_result({"ok": True, "data": {"record_id": "task-1"}}),
            cli_result({"ok": True, "data": {"record_id": "task-1"}}),
        ])
        client = LarkBaseClient(runner=runner)

        client.set_task_status("task-1", "成功")
        client.set_task_status("task-1", "失败")
        for invalid in ("未开始", "", ["成功"]):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    client.set_task_status("task-1", invalid)  # type: ignore[arg-type]

        self.assertEqual(2, len(runner.calls))
        for index, status in enumerate(("成功", "失败")):
            args = runner.calls[index][0]
            self.assertEqual("task-1", args[args.index("--record-id") + 1])
            self.assertEqual(
                json.dumps({"任务状态": [status]}, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                args[args.index("--json") + 1],
            )

    def test_download_sanitizes_name_uses_one_file_token_and_requires_nonempty_file(self):
        task = sample_task(image_name="../.hidden\\look.jpg\x00")

        def write_download(args, kwargs):
            write_download_output(args, kwargs, b"downloaded")
            return cli_result({"ok": True, "data": {}})

        runner = FakeRunner([write_download])
        destination_dir = self.root / "task-scope"

        downloaded = LarkBaseClient(runner=runner).download_source_image(task, destination_dir)

        self.assertEqual(destination_dir, downloaded.parent)
        self.assertTrue(downloaded.is_file())
        self.assertGreater(downloaded.stat().st_size, 0)
        self.assertNotIn("/", downloaded.name)
        self.assertNotIn("\\", downloaded.name)
        self.assertNotIn("\x00", downloaded.name)
        self.assertFalse(downloaded.name.startswith("."))
        args = runner.calls[0][0]
        self.assertEqual(1, args.count("--file-token"))
        self.assertEqual("file-token-9", args[args.index("--file-token") + 1])
        self.assertEqual(downloaded.name, args[args.index("--output") + 1])
        kwargs = runner.calls[0][1]
        self.assertNotIn("cwd", kwargs)
        self.assertEqual(1, len(kwargs["pass_fds"]))
        self.assertTrue(callable(kwargs["preexec_fn"]))
        self.assertNotIn("--overwrite", args)

    def test_download_adds_overwrite_only_for_an_existing_ordinary_file_and_rejects_empty_output(self):
        destination = self.root / "task-scope"
        destination.mkdir()
        existing = destination / "look.jpg"
        existing.write_bytes(b"old")

        def overwrite(args, kwargs):
            self.assertNotIn("--overwrite", args)
            write_download_output(args, kwargs, b"new")
            return cli_result({"ok": True, "data": {}})

        client = LarkBaseClient(runner=FakeRunner([overwrite]))
        self.assertEqual(existing, client.download_source_image(sample_task(), destination))
        self.assertEqual(b"new", existing.read_bytes())

        empty_dir = self.root / "empty-scope"
        empty_runner = FakeRunner([cli_result({"ok": True, "data": {}})])
        with self.assertRaises(LarkBaseError):
            LarkBaseClient(runner=empty_runner).download_source_image(sample_task(), empty_dir)

    def test_download_rejects_symlink_non_directory_and_traversal_destinations(self):
        ordinary_file = self.root / "ordinary-file"
        ordinary_file.write_text("not a directory")
        real_dir = self.root / "real-dir"
        real_dir.mkdir()
        symlink_dir = self.root / "linked-dir"
        symlink_dir.symlink_to(real_dir, target_is_directory=True)
        traversal = self.root / "scope" / ".." / "escaped"

        for destination in (ordinary_file, symlink_dir, traversal):
            with self.subTest(destination=destination):
                runner = FakeRunner([])
                with self.assertRaises(LarkBaseError):
                    LarkBaseClient(runner=runner).download_source_image(
                        sample_task(), destination,
                    )
                self.assertEqual([], runner.calls)

    def test_download_failure_does_not_expose_raw_cli_validation_body(self):
        runner = FakeRunner([cli_result(
            {"ok": False, "data": {}},
            returncode=2,
            stderr="unsafe output path /private/secret-location must be relative",
        )])

        with self.assertRaises(LarkBaseError) as raised:
            LarkBaseClient(runner=runner).download_source_image(
                sample_task(), self.root / "safe-scope",
            )

        message = str(raised.exception)
        self.assertIn("task attachment download", message)
        self.assertNotIn("unsafe output", message)
        self.assertNotIn("secret-location", message)
        args, kwargs = runner.calls[0]
        self.assertEqual("look.jpg", args[args.index("--output") + 1])
        self.assertNotIn("cwd", kwargs)
        self.assertEqual(1, len(kwargs["pass_fds"]))

    def test_download_never_follows_existing_final_symlink(self):
        destination = self.root / "symlink-scope"
        destination.mkdir()
        victim = self.root / "victim.jpg"
        victim.write_bytes(b"victim")
        final = destination / "look.jpg"
        final.symlink_to(victim)

        def download(args, kwargs):
            write_download_output(args, kwargs, b"safe")
            return cli_result({"ok": True, "data": {}})

        downloaded = LarkBaseClient(
            runner=FakeRunner([download]),
        ).download_source_image(sample_task(), destination)

        self.assertEqual(b"victim", victim.read_bytes())
        self.assertFalse(downloaded.is_symlink())
        self.assertEqual(b"safe", downloaded.read_bytes())

    def test_download_file_swap_and_restore_cannot_overwrite_victim(self):
        destination = self.root / "file-swap-scope"
        destination.mkdir()
        final = destination / "look.jpg"
        final.write_bytes(b"old-final")
        backup = destination / "look.backup"
        victim = self.root / "swap-victim.jpg"
        victim.write_bytes(b"victim")

        def swap_restore(args, kwargs):
            final.rename(backup)
            final.symlink_to(victim)
            write_download_output(args, kwargs, b"downloaded")
            final.unlink()
            backup.rename(final)
            return cli_result({"ok": True, "data": {}})

        downloaded = LarkBaseClient(
            runner=FakeRunner([swap_restore]),
        ).download_source_image(sample_task(), destination)

        self.assertEqual(b"victim", victim.read_bytes())
        self.assertEqual(b"downloaded", downloaded.read_bytes())

    def test_download_directory_swap_and_restore_publishes_to_bound_directory(self):
        destination = self.root / "directory-swap-scope"
        destination.mkdir()
        final = destination / "look.jpg"
        final.write_bytes(b"old-final")
        backup = self.root / "directory-swap-backup"

        def swap_restore(args, kwargs):
            destination.rename(backup)
            destination.mkdir()
            write_download_output(args, kwargs, b"downloaded")
            destination.rmdir()
            backup.rename(destination)
            return cli_result({"ok": True, "data": {}})

        downloaded = LarkBaseClient(
            runner=FakeRunner([swap_restore]),
        ).download_source_image(sample_task(), destination)

        self.assertEqual(b"downloaded", downloaded.read_bytes())

    def test_download_staging_swap_and_restore_cannot_write_external_victim(self):
        destination = self.root / "staging-swap-scope"
        destination.mkdir()
        victim_dir = self.root / "staging-victim"
        victim_dir.mkdir()
        staging_backup = self.root / "staging-backup"

        def swap_restore(args, kwargs):
            [staging_path] = list(self.root.glob(".find-best-seller-download-*"))
            staging_path.rename(staging_backup)
            staging_path.symlink_to(victim_dir, target_is_directory=True)
            write_download_output(args, kwargs, b"downloaded")
            staging_path.unlink()
            staging_backup.rename(staging_path)
            return cli_result({"ok": True, "data": {}})

        downloaded = LarkBaseClient(
            runner=FakeRunner([swap_restore]),
        ).download_source_image(sample_task(), destination)

        self.assertFalse((victim_dir / "look.jpg").exists())
        self.assertEqual(b"downloaded", downloaded.read_bytes())

    def test_download_revalidates_destination_path_after_publication(self):
        destination = self.root / "publication-swap-scope"
        destination.mkdir()
        backup = self.root / "publication-swap-backup"
        real_rename = os.rename

        def publish_then_swap(*args, **kwargs):
            result = real_rename(*args, **kwargs)
            real_rename(destination, backup)
            os.mkdir(destination)
            return result

        def download(args, kwargs):
            write_download_output(args, kwargs, b"downloaded")
            return cli_result({"ok": True, "data": {}})

        try:
            with mock.patch("scripts.lark_base.os.rename", side_effect=publish_then_swap):
                with self.assertRaises(LarkBaseError):
                    LarkBaseClient(runner=FakeRunner([download])).download_source_image(
                        sample_task(), destination,
                    )
            self.assertFalse((destination / "look.jpg").exists())
            self.assertEqual(b"downloaded", (backup / "look.jpg").read_bytes())
        finally:
            if destination.exists():
                destination.rmdir()
            if backup.exists():
                backup.rename(destination)

    def test_download_directory_fsync_failure_reports_after_atomic_publication(self):
        destination = self.root / "fsync-failure-scope"
        destination.mkdir()
        existing = destination / "look.jpg"
        existing.write_bytes(b"old")
        real_fsync = os.fsync

        def fail_directory_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("raw-fsync-detail")
            return real_fsync(fd)

        def download(args, kwargs):
            write_download_output(args, kwargs, b"downloaded")
            return cli_result({"ok": True, "data": {}})

        with mock.patch("scripts.lark_base.os.fsync", side_effect=fail_directory_fsync):
            with self.assertRaises(LarkBaseError) as raised:
                LarkBaseClient(runner=FakeRunner([download])).download_source_image(
                    sample_task(), destination,
                )

        self.assertNotIn("raw-fsync-detail", str(raised.exception))
        self.assertEqual(b"downloaded", existing.read_bytes())


if __name__ == "__main__":
    unittest.main()
