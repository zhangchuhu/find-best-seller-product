"""Safe deterministic adapter for the approved Lark Base tables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from .candidates import visual_feature_text
from .metrics import parse_count, parse_rating
from .models import Task, VerifiedCandidate
from .platforms import Platform, canonicalize_url, route_platform


TASK_BASE_TOKEN = "QV65bn30QalwojsKDEicaRP9nne"
TASK_TABLE_ID = "tblwFB5IgrLwTP5P"
RESULT_BASE_TOKEN = "SIoUbFgwQaGumXs9U6FccFs8nYc"
RESULT_TABLE_ID = "tblL8RyyMnhFeqaX"

TASK_FIELDS = {
    "SKU": "text", "原图": "attachment", "评分": "text", "评价数": "text",
    "结果数量": "text", "vendidos 数": "text", "平台": "text", "任务状态": "select",
}
RESULT_FIELDS = {
    "SKU": "text", "原图": "attachment", "平台": "text", "标题": "text",
    "爆款链接": "text", "vendidos 数": "text", "评价数": "text",
    "评分": "text", "视觉特征": "text",
}

_MAX_ERROR = 500
_MARKDOWN_LINK = re.compile(r"^\s*\[[^\]]*\]\((https?://[^)]+)\)\s*$")
_RESULT_COUNT = re.compile(r"^\+?\d+$")
_FIELD_TYPE = re.compile(r"^[a-z][a-z0-9_-]*$")
_MATRIX_ONLY_KEYS = frozenset({"data", "field_type_list", "record_id_list", "has_more"})
_SECRET_PATTERNS = (
    re.compile(r"(?i)(--(?:base-)?token\s+)[^\s]+"),
    re.compile(r"(?i)(--json\s+)[^\s]+"),
    re.compile(r"(?i)(authorization\s*:?\s*(?:bearer\s+)?)[^\s,;}]+"),
    re.compile(r"(?i)([\"']?(?:token|secret|api[-_ ]?key)[\"']?\s*[:=]\s*[\"']?)[^\s,;}\"']+"),
)


class LarkBaseError(RuntimeError):
    """Raised when the CLI boundary or Base contract is unsafe or malformed."""


@dataclass(frozen=True)
class TaskFailure:
    record_id: str
    message: str
    terminal: bool = False


@dataclass(frozen=True)
class ResultWrite:
    record_id: str
    created: bool
    attachment_uploaded: bool
    verified_fields: tuple[str, ...]


def _bounded(text: object) -> str:
    value = str(text)
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(r"\1[REDACTED]", value)
    return value[:_MAX_ERROR]


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise LarkBaseError(f"{label} must be a list")
    return value


def _bind_child_cwd(directory_fd: int) -> None:
    """Run after fork: bind the child to an already-open directory, then close it."""
    os.fchdir(directory_fd)
    os.close(directory_fd)


class LarkBaseClient:
    def __init__(self, executable="lark-cli", runner=subprocess.run, timeout=30.0):
        if not isinstance(executable, str) or not executable.strip():
            raise TypeError("executable must be a non-empty string")
        if not callable(runner):
            raise TypeError("runner must be callable")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        self._executable = executable
        self._runner = runner
        self._timeout = float(timeout)

    def _invoke(
        self,
        label: str,
        arguments: list[str],
        *,
        cwd: str | None = None,
        cwd_fd: int | None = None,
        include_failure_detail: bool = True,
    ) -> Mapping[str, object]:
        command = [self._executable, "base", *arguments]
        runner_options: dict[str, object] = {
            "shell": False,
            "check": False,
            "text": True,
            "capture_output": True,
            "timeout": self._timeout,
        }
        if cwd is not None and cwd_fd is not None:
            raise LarkBaseError(f"{label} has conflicting working directories")
        if cwd is not None:
            if not isinstance(cwd, str) or not cwd or not Path(cwd).is_absolute():
                raise LarkBaseError(f"{label} has an invalid working directory")
            runner_options["cwd"] = cwd
        if cwd_fd is not None:
            if (
                os.name != "posix"
                or not hasattr(os, "fchdir")
                or type(cwd_fd) is not int
                or cwd_fd < 0
            ):
                raise LarkBaseError(f"{label} cannot bind a safe working directory")
            try:
                cwd_stat = os.fstat(cwd_fd)
            except OSError:
                raise LarkBaseError(f"{label} cannot bind a safe working directory") from None
            if not stat.S_ISDIR(cwd_stat.st_mode):
                raise LarkBaseError(f"{label} cannot bind a safe working directory")
            runner_options["pass_fds"] = (cwd_fd,)
            runner_options["preexec_fn"] = partial(_bind_child_cwd, cwd_fd)
        try:
            completed = self._runner(command, **runner_options)
        except Exception as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                category = "timed out"
            elif isinstance(exc, FileNotFoundError):
                category = "executable unavailable"
            elif isinstance(exc, PermissionError):
                category = "permission denied"
            elif isinstance(exc, OSError):
                category = "operating-system failure"
            else:
                category = "runner failure"
            raise LarkBaseError(f"{label} {category}") from None
        returncode = getattr(completed, "returncode", None)
        stdout = getattr(completed, "stdout", "")
        stderr = getattr(completed, "stderr", "")
        if returncode != 0:
            if not include_failure_detail:
                raise LarkBaseError(f"{label} failed with exit {returncode}")
            detail = _bounded(stderr or stdout or "no output")
            raise LarkBaseError(_bounded(f"{label} failed with exit {returncode}: {detail}"))
        try:
            envelope = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LarkBaseError(f"{label} returned non-JSON output") from exc
        if not isinstance(envelope, dict):
            raise LarkBaseError(f"{label} returned a non-object envelope")
        if envelope.get("ok") is not True:
            raise LarkBaseError(f"{label} returned an unsuccessful envelope")
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise LarkBaseError(f"{label} returned malformed data")
        return data

    def _matrix(self, label: str, data: Mapping[str, object]) -> tuple[list[dict[str, object]], bool]:
        fields = _sequence(data.get("fields"), f"{label} fields")
        types = _sequence(data.get("field_type_list"), f"{label} field types")
        rows = _sequence(data.get("data"), f"{label} rows")
        record_ids = _sequence(data.get("record_id_list"), f"{label} record IDs")
        if len(fields) != len(types):
            raise LarkBaseError(f"{label} field/type matrix lengths differ")
        if len(rows) != len(record_ids):
            raise LarkBaseError(f"{label} row/record matrix lengths differ")
        if any(not isinstance(field, str) or not field for field in fields):
            raise LarkBaseError(f"{label} contains an invalid field name")
        if len(set(fields)) != len(fields):
            raise LarkBaseError(f"{label} contains duplicate field names")
        if any(not isinstance(field_type, str) or not field_type for field_type in types):
            raise LarkBaseError(f"{label} contains an invalid field type")
        converted: list[dict[str, object]] = []
        for index, (row, record_id) in enumerate(zip(rows, record_ids, strict=True)):
            if not isinstance(row, list) or len(row) != len(fields):
                raise LarkBaseError(f"{label} row {index} has a mismatched matrix length")
            if not isinstance(record_id, str) or not record_id.strip():
                raise LarkBaseError(f"{label} row {index} has an invalid record ID")
            item = dict(zip(fields, row, strict=True))
            item["_record_id"] = record_id.strip()
            converted.append(item)
        has_more = data.get("has_more", False)
        if type(has_more) is not bool:
            raise LarkBaseError(f"{label} has invalid pagination state")
        if has_more and not converted:
            raise LarkBaseError(f"{label} pagination stalled with zero rows")
        return converted, has_more

    def _read_matrix(self, label: str, arguments: list[str]) -> tuple[list[dict[str, object]], bool, Mapping[str, object]]:
        data = self._invoke(label, [*arguments, "--format", "json", "--as", "user"])
        rows, has_more = self._matrix(label, data)
        return rows, has_more, data

    def _read_field_list(self, label: str, arguments: list[str]) -> dict[str, str]:
        data = self._invoke(label, [*arguments, "--format", "json", "--as", "user"])
        if _MATRIX_ONLY_KEYS.intersection(data):
            raise LarkBaseError(f"{label} returned an ambiguous matrix shape")
        fields = _sequence(data.get("fields"), f"{label} fields")
        if "total" not in data:
            raise LarkBaseError(f"{label} has no total")
        total = data["total"]
        if type(total) is not int or total != len(fields):
            raise LarkBaseError(f"{label} has an inconsistent total")

        actual: dict[str, str] = {}
        field_ids: set[str] = set()
        for index, field in enumerate(fields):
            if not isinstance(field, Mapping):
                raise LarkBaseError(f"{label} field {index} must be an object")
            field_id = field.get("id")
            name = field.get("name")
            field_type = field.get("type")
            if (
                not isinstance(field_id, str)
                or not field_id
                or field_id != field_id.strip()
            ):
                raise LarkBaseError(f"{label} field {index} has an invalid ID")
            if (
                not isinstance(name, str)
                or not name
                or name != name.strip()
            ):
                raise LarkBaseError(f"{label} field {index} has an invalid name")
            if (
                not isinstance(field_type, str)
                or field_type != field_type.strip()
                or _FIELD_TYPE.fullmatch(field_type) is None
            ):
                raise LarkBaseError(f"{label} field {index} has an invalid type")
            if field_id in field_ids:
                raise LarkBaseError(f"{label} contains duplicate field IDs")
            if name in actual:
                raise LarkBaseError(f"{label} contains duplicate field names")
            field_ids.add(field_id)
            actual[name] = field_type
        return actual

    def validate_base_contracts(self) -> None:
        errors: list[str] = []
        bases = (
            ("task", TASK_BASE_TOKEN, TASK_TABLE_ID, TASK_FIELDS),
            ("result", RESULT_BASE_TOKEN, RESULT_TABLE_ID, RESULT_FIELDS),
        )
        for label, token, table_id, required in bases:
            actual = self._read_field_list(
                f"{label} field-list",
                [
                    "+field-list", "--base-token", token,
                    "--table-id", table_id,
                ],
            )
            for name, expected_type in required.items():
                if name not in actual:
                    errors.append(f"{label}: missing {name}")
                elif actual[name] != expected_type:
                    errors.append(
                        f"{label}: {name} expected {expected_type}, got {actual[name]}"
                    )
        if errors:
            raise LarkBaseError(_bounded("Base contract mismatch: " + "; ".join(errors)))

    def _task_rows(self, record_id: str | None) -> list[dict[str, object]]:
        if record_id is not None:
            if not isinstance(record_id, str) or not record_id.strip():
                raise TypeError("record_id must be a non-empty string")
            rows, _, _ = self._read_matrix(
                "task record-get",
                [
                    "+record-get", "--base-token", TASK_BASE_TOKEN,
                    "--table-id", TASK_TABLE_ID, "--record-id", record_id.strip(),
                ],
            )
            return rows

        result: list[dict[str, object]] = []
        offset = 0
        while True:
            rows, has_more, _ = self._read_matrix(
                "task record-list",
                [
                    "+record-list", "--base-token", TASK_BASE_TOKEN,
                    "--table-id", TASK_TABLE_ID, "--offset", str(offset),
                    "--limit", "200",
                ],
            )
            result.extend(rows)
            if not has_more:
                return result
            offset += len(rows)

    @staticmethod
    def _pending(row: Mapping[str, object]) -> bool:
        return row.get("任务状态") == ["未开始"]

    @staticmethod
    def _task_from_row(row: Mapping[str, object]) -> Task:
        record_id = row.get("_record_id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("record ID is missing")
        sku = row.get("SKU")
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError("SKU must be non-empty")
        attachments = row.get("原图")
        if not isinstance(attachments, list) or not attachments:
            raise ValueError("原图 must contain at least one attachment")
        first = attachments[0]
        if not isinstance(first, Mapping):
            raise ValueError("the first 原图 attachment is malformed")
        image_token = first.get("file_token")
        image_name = first.get("name")
        if not isinstance(image_token, str) or not image_token.strip():
            raise ValueError("the first 原图 file_token is missing")
        if not isinstance(image_name, str) or not image_name.strip():
            raise ValueError("the first 原图 name is missing")
        platform_value = row.get("平台")
        if not isinstance(platform_value, str) or not platform_value.strip():
            raise ValueError("平台 must be non-empty text")
        markdown = _MARKDOWN_LINK.fullmatch(platform_value)
        platform_url = markdown.group(1) if markdown else platform_value.strip()
        platform = route_platform(platform_url)

        sold_text = row.get("vendidos 数")
        reviews_text = row.get("评价数")
        rating_text = row.get("评分")
        sold = parse_count(sold_text) if isinstance(sold_text, str) else None
        reviews = parse_count(reviews_text) if isinstance(reviews_text, str) else None
        rating = parse_rating(rating_text) if isinstance(rating_text, str) else None
        if sold is None:
            raise ValueError("vendidos 数 is missing or ambiguous")
        if reviews is None:
            raise ValueError("评价数 is missing or ambiguous")
        if rating is None:
            raise ValueError("评分 is missing or outside 0..5")
        count_text = row.get("结果数量")
        if not isinstance(count_text, str) or _RESULT_COUNT.fullmatch(count_text.strip()) is None:
            raise ValueError("结果数量 must be a positive integer")
        result_count = int(count_text.strip().lstrip("+"))
        if result_count <= 0:
            raise ValueError("结果数量 must be a positive integer")
        return Task(
            record_id=record_id,
            sku=sku,
            image_token=image_token,
            image_name=image_name,
            platform=platform,
            min_sold=sold,
            min_reviews=reviews,
            min_rating=rating,
            result_limit=result_count,
        )

    def list_pending_tasks(self, record_id: str | None = None) -> tuple[list[Task], list[TaskFailure]]:
        rows = self._task_rows(record_id)
        if record_id is not None:
            requested_id = record_id.strip()
            if (
                len(rows) != 1
                or rows[0].get("_record_id") != requested_id
                or not self._pending(rows[0])
            ):
                return [], [TaskFailure(requested_id, "record scope is missing or not pending", False)]
        tasks: list[Task] = []
        failures: list[TaskFailure] = []
        for row in rows:
            if not self._pending(row):
                continue
            row_id = row.get("_record_id")
            safe_id = row_id if isinstance(row_id, str) and row_id else "unknown"
            try:
                tasks.append(self._task_from_row(row))
            except (TypeError, ValueError) as exc:
                failures.append(TaskFailure(safe_id, _bounded(exc), True))
        return tasks, failures

    def get_task_state(self, record_id: str) -> tuple[Task, str]:
        if not isinstance(record_id, str) or not record_id.strip():
            raise TypeError("record_id must be a non-empty string")
        requested_id = record_id.strip()
        rows = self._task_rows(requested_id)
        if len(rows) != 1 or rows[0].get("_record_id") != requested_id:
            raise LarkBaseError("task record state is missing or ambiguous")
        status = rows[0].get("任务状态")
        if (
            not isinstance(status, list)
            or len(status) != 1
            or status[0] not in {"未开始", "成功", "失败"}
        ):
            raise LarkBaseError("task record state is invalid")
        try:
            task = self._task_from_row(rows[0])
        except (TypeError, ValueError):
            raise LarkBaseError("task record state is invalid") from None
        return task, status[0]

    def download_source_image(self, task: Task, destination_dir: Path) -> Path:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        return self._download_attachment(
            base_token=TASK_BASE_TOKEN,
            table_id=TASK_TABLE_ID,
            record_id=task.record_id,
            file_token=task.image_token,
            file_name=task.image_name,
            destination_dir=destination_dir,
            label="task attachment download",
        )

    def _download_attachment(
        self,
        *,
        base_token: str,
        table_id: str,
        record_id: str,
        file_token: str,
        file_name: str,
        destination_dir: Path,
        label: str,
    ) -> Path:
        for value, field in (
            (base_token, "base_token"),
            (table_id, "table_id"),
            (record_id, "record_id"),
            (file_token, "file_token"),
            (label, "label"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise TypeError(f"{field} must be a non-empty string")
        if not isinstance(destination_dir, Path):
            raise TypeError("destination_dir must be a Path")
        sanitized = self._sanitize_attachment_name(file_name)
        if ".." in destination_dir.parts:
            raise LarkBaseError("destination_dir contains traversal")
        try:
            if destination_dir.is_symlink():
                raise LarkBaseError("destination_dir must not be a symlink")
            if destination_dir.exists() and not destination_dir.is_dir():
                raise LarkBaseError("destination_dir is not a directory")
            destination_dir.mkdir(parents=True, exist_ok=True)
            if destination_dir.is_symlink() or not destination_dir.is_dir():
                raise LarkBaseError("destination_dir is not a safe directory")
            resolved_dir = destination_dir.resolve(strict=True)
            directory_stat = resolved_dir.stat()
        except LarkBaseError:
            raise
        except OSError:
            raise LarkBaseError("destination_dir could not be prepared safely") from None
        destination = destination_dir / sanitized
        destination_fd: int | None = None
        staging_fd: int | None = None
        staging_path: Path | None = None
        staging_stat: os.stat_result | None = None
        try:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            destination_fd = os.open(resolved_dir, directory_flags)
            bound_directory_stat = os.fstat(destination_fd)
            if (
                (bound_directory_stat.st_dev, bound_directory_stat.st_ino)
                != (directory_stat.st_dev, directory_stat.st_ino)
            ):
                raise LarkBaseError("destination_dir changed before attachment download")

            staging_path = Path(tempfile.mkdtemp(
                prefix=".find-best-seller-download-",
                dir=resolved_dir.parent,
            ))
            os.chmod(staging_path, 0o700, follow_symlinks=False)
            staging_path = staging_path.resolve(strict=True)
            staging_fd = os.open(staging_path, directory_flags)
            staging_stat = os.fstat(staging_fd)

            arguments = [
                "+record-download-attachment",
                "--base-token", base_token,
                "--table-id", table_id,
                "--record-id", record_id,
                "--file-token", file_token,
                "--output", sanitized,
                "--format", "json", "--as", "user",
            ]
            self._invoke(
                label,
                arguments,
                cwd_fd=staging_fd,
                include_failure_detail=False,
            )

            visible_staging_stat = os.stat(staging_path, follow_symlinks=False)
            if (
                not stat.S_ISDIR(visible_staging_stat.st_mode)
                or (visible_staging_stat.st_dev, visible_staging_stat.st_ino)
                != (staging_stat.st_dev, staging_stat.st_ino)
            ):
                raise LarkBaseError("attachment staging directory changed during download")

            staged_fd = os.open(
                sanitized,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=staging_fd,
            )
            try:
                staged_stat = os.fstat(staged_fd)
                if not stat.S_ISREG(staged_stat.st_mode):
                    raise LarkBaseError("task attachment download produced no ordinary file")
                if staged_stat.st_size <= 0:
                    raise LarkBaseError("task attachment download produced an empty file")
                os.fsync(staged_fd)
            finally:
                os.close(staged_fd)

            current_directory_stat = os.stat(resolved_dir, follow_symlinks=False)
            bound_directory_stat = os.fstat(destination_fd)
            if (
                not stat.S_ISDIR(current_directory_stat.st_mode)
                or (current_directory_stat.st_dev, current_directory_stat.st_ino)
                != (bound_directory_stat.st_dev, bound_directory_stat.st_ino)
                or (bound_directory_stat.st_dev, bound_directory_stat.st_ino)
                != (directory_stat.st_dev, directory_stat.st_ino)
            ):
                raise LarkBaseError("destination_dir changed during attachment download")

            os.rename(
                sanitized,
                sanitized,
                src_dir_fd=staging_fd,
                dst_dir_fd=destination_fd,
            )
            published_fd = os.open(
                sanitized,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=destination_fd,
            )
            try:
                published_stat = os.fstat(published_fd)
                if (
                    not stat.S_ISREG(published_stat.st_mode)
                    or (published_stat.st_dev, published_stat.st_ino)
                    != (staged_stat.st_dev, staged_stat.st_ino)
                    or published_stat.st_size <= 0
                ):
                    raise LarkBaseError("task attachment publication could not be verified")
            finally:
                os.close(published_fd)
            os.fsync(destination_fd)

            visible_destination_stat = os.stat(resolved_dir, follow_symlinks=False)
            bound_directory_stat = os.fstat(destination_fd)
            if (
                not stat.S_ISDIR(visible_destination_stat.st_mode)
                or (visible_destination_stat.st_dev, visible_destination_stat.st_ino)
                != (bound_directory_stat.st_dev, bound_directory_stat.st_ino)
                or (bound_directory_stat.st_dev, bound_directory_stat.st_ino)
                != (directory_stat.st_dev, directory_stat.st_ino)
            ):
                raise LarkBaseError("destination_dir changed after attachment publication")
        except LarkBaseError:
            raise
        except OSError:
            raise LarkBaseError("task attachment download could not be staged safely") from None
        finally:
            if staging_fd is not None:
                try:
                    os.unlink(sanitized, dir_fd=staging_fd)
                except OSError:
                    pass
                try:
                    os.close(staging_fd)
                except OSError:
                    pass
            if staging_path is not None and staging_stat is not None:
                try:
                    visible = os.stat(staging_path, follow_symlinks=False)
                    if (
                        stat.S_ISDIR(visible.st_mode)
                        and (visible.st_dev, visible.st_ino)
                        == (staging_stat.st_dev, staging_stat.st_ino)
                    ):
                        os.rmdir(staging_path)
                except OSError:
                    pass
            if destination_fd is not None:
                try:
                    os.close(destination_fd)
                except OSError:
                    pass
        return destination

    @staticmethod
    def _sanitize_attachment_name(original: str) -> str:
        if not isinstance(original, str) or not original:
            raise LarkBaseError("attachment name is empty")
        value = original.replace("\x00", "").replace("/", "_").replace("\\", "_")
        value = value.lstrip(".")
        value = re.sub(r"\.{2,}", ".", value)
        value = value.strip()
        if value in {"", ".", ".."}:
            raise LarkBaseError("attachment name becomes empty after sanitization")
        return value

    @staticmethod
    def _platform_label(platform: Platform) -> str:
        if platform is Platform.MERCADO_MX:
            return "Mercado Libre México"
        if platform is Platform.SHEIN_US:
            return "SHEIN US"
        raise TypeError("platform must be a supported Platform")

    def _result_rows(self) -> list[dict[str, object]]:
        rows_out: list[dict[str, object]] = []
        offset = 0
        while True:
            rows, has_more, _ = self._read_matrix(
                "result record-list",
                [
                    "+record-list", "--base-token", RESULT_BASE_TOKEN,
                    "--table-id", RESULT_TABLE_ID, "--offset", str(offset),
                    "--limit", "200",
                ],
            )
            rows_out.extend(rows)
            if not has_more:
                return rows_out
            offset += len(rows)

    def find_existing_result(self, sku: str, platform: Platform, canonical_url: str) -> Mapping[str, object] | None:
        if not isinstance(sku, str) or not sku.strip():
            raise TypeError("sku must be a non-empty string")
        if not isinstance(platform, Platform):
            raise TypeError("platform must be a Platform")
        wanted_url = canonicalize_url(platform, canonical_url)
        wanted_label = self._platform_label(platform)
        matches: list[dict[str, object]] = []
        for row in self._result_rows():
            if row.get("SKU") != sku.strip() or row.get("平台") != wanted_label:
                continue
            existing_url = row.get("爆款链接")
            if not isinstance(existing_url, str):
                continue
            try:
                normalized_existing = canonicalize_url(platform, existing_url)
            except (TypeError, ValueError):
                continue
            if normalized_existing == wanted_url:
                matches.append(row)
        if len(matches) > 1:
            raise LarkBaseError("duplicate result rows share the same business key")
        return matches[0] if matches else None

    def _get_result(self, record_id: str) -> dict[str, object]:
        rows, _, _ = self._read_matrix(
            "result record-get",
            [
                "+record-get", "--base-token", RESULT_BASE_TOKEN,
                "--table-id", RESULT_TABLE_ID, "--record-id", record_id,
            ],
        )
        if len(rows) != 1 or rows[0].get("_record_id") != record_id:
            raise LarkBaseError(f"result record-get did not return {record_id}")
        return rows[0]

    @staticmethod
    def _has_named_attachment(row: Mapping[str, object], names: set[str]) -> bool:
        attachments = row.get("原图")
        if not isinstance(attachments, list) or not attachments:
            return False
        return any(
            isinstance(item, Mapping)
            and isinstance(item.get("name"), str)
            and item.get("name") in names
            for item in attachments
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        descriptor = -1
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode) or status.st_size <= 0:
                raise OSError
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            raise LarkBaseError("attachment content could not be hashed safely") from None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _has_matching_attachment_content(
        self,
        row: Mapping[str, object],
        record_id: str,
        source_hash: str,
        temporary_root: Path,
        cache: dict[str, str],
    ) -> bool:
        attachments = row.get("原图")
        if not isinstance(attachments, list):
            return False
        for index, item in enumerate(attachments):
            if not isinstance(item, Mapping):
                continue
            token = item.get("file_token")
            name = item.get("name")
            if not isinstance(token, str) or not token.strip() or not isinstance(name, str) or not name.strip():
                continue
            normalized_token = token.strip()
            digest = cache.get(normalized_token)
            if digest is None:
                downloaded = self._download_attachment(
                    base_token=RESULT_BASE_TOKEN,
                    table_id=RESULT_TABLE_ID,
                    record_id=record_id,
                    file_token=normalized_token,
                    file_name=name,
                    destination_dir=temporary_root / f"attachment-{index}",
                    label="result attachment download",
                )
                digest = self._file_sha256(downloaded)
                cache[normalized_token] = digest
            if digest == source_hash:
                return True
        return False

    @staticmethod
    def _created_record_id(data: Mapping[str, object]) -> str:
        direct = data.get("record_id")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        identifiers = data.get("record_id_list")
        if (
            isinstance(identifiers, list)
            and len(identifiers) == 1
            and isinstance(identifiers[0], str)
            and identifiers[0].strip()
        ):
            return identifiers[0].strip()
        raise LarkBaseError("result record-upsert returned no record ID")

    @staticmethod
    def _result_payload(task: Task, candidate: VerifiedCandidate) -> dict[str, str]:
        if candidate.platform is not task.platform:
            raise ValueError("candidate platform does not match task platform")
        canonical = canonicalize_url(candidate.platform, candidate.canonical_url)
        return {
            "SKU": task.sku,
            "平台": LarkBaseClient._platform_label(candidate.platform),
            "标题": candidate.title,
            "爆款链接": canonical,
            "vendidos 数": candidate.sold_display or "",
            "评价数": candidate.reviews_display or "",
            "评分": candidate.rating_display or "",
            "视觉特征": visual_feature_text(candidate),
        }

    def write_result(
        self,
        task: Task,
        candidate: VerifiedCandidate,
        source_image: Path,
        dry_run: bool = False,
    ) -> ResultWrite:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        if not isinstance(candidate, VerifiedCandidate):
            raise TypeError("candidate must be a VerifiedCandidate")
        if not isinstance(source_image, Path):
            raise TypeError("source_image must be a Path")
        if type(dry_run) is not bool:
            raise TypeError("dry_run must be a boolean")
        payload = self._result_payload(task, candidate)
        existing = self.find_existing_result(
            task.sku, candidate.platform, payload["爆款链接"],
        )
        if dry_run:
            return ResultWrite("dry-run", False, False, ())

        existing_id = existing.get("_record_id") if existing is not None else None
        if existing_id is not None and (not isinstance(existing_id, str) or not existing_id):
            raise LarkBaseError("existing result has an invalid record ID")
        created = existing_id is None
        arguments = [
            "+record-upsert", "--base-token", RESULT_BASE_TOKEN,
            "--table-id", RESULT_TABLE_ID,
        ]
        if existing_id is not None:
            arguments.extend(["--record-id", existing_id])
        arguments.extend([
            "--json",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            "--format", "json", "--as", "user",
        ])

        result_id: str | None = existing_id
        try:
            upsert_data = self._invoke("result record-upsert", arguments)
            if result_id is None:
                result_id = self._created_record_id(upsert_data)
            current = existing if existing is not None else self._get_result(result_id)
            source_hash = self._file_sha256(source_image)
            attachment_uploaded = False
            hash_cache: dict[str, str] = {}
            with tempfile.TemporaryDirectory(
                prefix=".find-best-seller-verify-",
                dir=source_image.parent,
            ) as temporary_name:
                temporary_root = Path(temporary_name)
                has_source = self._has_matching_attachment_content(
                    current, result_id, source_hash, temporary_root, hash_cache,
                )
                if not has_source:
                    self._invoke(
                        "result attachment upload",
                        [
                            "+record-upload-attachment",
                            "--base-token", RESULT_BASE_TOKEN,
                            "--table-id", RESULT_TABLE_ID,
                            "--record-id", result_id,
                            "--field-id", "原图",
                            "--file", str(source_image),
                            "--format", "json", "--as", "user",
                        ],
                    )
                    attachment_uploaded = True
                verified = self._get_result(result_id)
                mismatches = [name for name, value in payload.items() if verified.get(name) != value]
                if not self._has_matching_attachment_content(
                    verified, result_id, source_hash, temporary_root, {},
                ):
                    mismatches.append("原图")
            if mismatches:
                raise LarkBaseError(
                    _bounded(f"result {result_id} readback mismatch: {', '.join(sorted(mismatches))}")
                )
            return ResultWrite(
                record_id=result_id,
                created=created,
                attachment_uploaded=attachment_uploaded,
                verified_fields=tuple(sorted(RESULT_FIELDS)),
            )
        except LarkBaseError as exc:
            if result_id is not None and result_id not in str(exc):
                raise LarkBaseError(f"result {result_id} partial failure") from None
            raise
        except Exception:
            if result_id is not None:
                raise LarkBaseError(f"result {result_id} partial failure") from None
            raise

    def verify_existing_result(
        self,
        task: Task,
        candidate: VerifiedCandidate,
        source_image: Path,
    ) -> bool:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        if not isinstance(candidate, VerifiedCandidate):
            raise TypeError("candidate must be a VerifiedCandidate")
        if not isinstance(source_image, Path):
            raise TypeError("source_image must be a Path")
        payload = self._result_payload(task, candidate)
        existing = self.find_existing_result(
            task.sku, candidate.platform, payload["爆款链接"],
        )
        if existing is None:
            return False
        record_id = existing.get("_record_id")
        if not isinstance(record_id, str) or not record_id:
            return False
        if any(existing.get(name) != value for name, value in payload.items()):
            return False
        source_hash = self._file_sha256(source_image)
        with tempfile.TemporaryDirectory(
            prefix=".find-best-seller-reconcile-",
            dir=source_image.parent,
        ) as temporary_name:
            return self._has_matching_attachment_content(
                existing,
                record_id,
                source_hash,
                Path(temporary_name),
                {},
            )
    def set_task_status(self, record_id: str, status: str, dry_run: bool = False) -> None:
        if not isinstance(record_id, str) or not record_id.strip():
            raise TypeError("record_id must be a non-empty string")
        if not isinstance(status, str):
            raise TypeError("status must be text")
        if status not in {"成功", "失败"}:
            raise ValueError("status must be 成功 or 失败")
        if type(dry_run) is not bool:
            raise TypeError("dry_run must be a boolean")
        if dry_run:
            return
        payload = json.dumps(
            {"任务状态": [status]},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._invoke(
            "task status update",
            [
                "+record-upsert", "--base-token", TASK_BASE_TOKEN,
                "--table-id", TASK_TABLE_ID,
                "--record-id", record_id.strip(),
                "--json", payload,
                "--format", "json", "--as", "user",
            ],
        )
