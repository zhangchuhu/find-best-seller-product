"""Safe validation for screenshot evidence files and screenshot regions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Mapping
from urllib.parse import urlsplit

from .platforms import Platform, route_platform


_DESCRIPTOR_KEYS = frozenset({"id", "kind", "file", "sha256", "page_url", "query", "identity"})
_REGION_KEYS = frozenset({"screenshot_id", "bbox"})
_MAX_SCREENSHOTS = 128
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_MAX_DIMENSION = 16_384
_READ_CHUNK_SIZE = 64 * 1024
_HEADER_LIMIT = _MAX_FILE_BYTES
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class ScreenshotEvidenceError(ValueError):
    """Raised when screenshot evidence cannot safely be accepted."""


@dataclass(frozen=True)
class ScreenshotProof:
    screenshot_id: str
    kind: str
    relative_path: str
    sha256: str
    page_url: str
    query: str | None
    identity: str | None
    media_type: str
    width: int
    height: int
    byte_size: int


def validate_screenshot_registry(
    raw: object,
    run_dir: Path,
    platform: Platform,
    queries: tuple[str, str, str],
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, ScreenshotProof]]:
    """Return normalized input descriptors, derived manifest, and proof lookup."""
    if not isinstance(raw, list) or len(raw) > _MAX_SCREENSHOTS:
        raise ScreenshotEvidenceError("screenshot registry is invalid")
    if not isinstance(platform, Platform) or not _valid_queries(queries):
        raise ScreenshotEvidenceError("screenshot registry is invalid")

    directory_fd = _open_screenshot_directory(Path(run_dir))
    try:
        total_bytes = 0
        descriptors: list[dict[str, object]] = []
        manifest: list[dict[str, object]] = []
        proofs: dict[str, ScreenshotProof] = {}
        paths: set[str] = set()
        for value in raw:
            descriptor = _parse_descriptor(value, platform, queries)
            screenshot_id = descriptor["id"]
            relative_path = descriptor["file"]
            if screenshot_id in proofs or relative_path in paths:
                raise ScreenshotEvidenceError("screenshot registry is invalid")
            paths.add(relative_path)
            byte_size, digest, header = _read_screenshot(directory_fd, relative_path)
            total_bytes += byte_size
            if total_bytes > _MAX_TOTAL_BYTES:
                raise ScreenshotEvidenceError("screenshot total is too large")
            if digest != descriptor["sha256"]:
                raise ScreenshotEvidenceError("screenshot digest does not match")
            media_type, width, height = _image_metadata(header)
            if not (1 <= width <= _MAX_DIMENSION and 1 <= height <= _MAX_DIMENSION):
                raise ScreenshotEvidenceError("screenshot dimensions are invalid")
            proof = ScreenshotProof(
                screenshot_id=screenshot_id,
                kind=descriptor["kind"],
                relative_path=relative_path,
                sha256=digest,
                page_url=descriptor["page_url"],
                query=descriptor["query"],
                identity=descriptor["identity"],
                media_type=media_type,
                width=width,
                height=height,
                byte_size=byte_size,
            )
            proofs[screenshot_id] = proof
            descriptors.append(dict(descriptor))
            manifest.append({
                "id": screenshot_id,
                "kind": proof.kind,
                "file": proof.relative_path,
                "sha256": proof.sha256,
                "page_url": proof.page_url,
                "query": proof.query,
                "identity": proof.identity,
                "media_type": proof.media_type,
                "width": proof.width,
                "height": proof.height,
                "byte_size": proof.byte_size,
            })
        return descriptors, manifest, proofs
    finally:
        os.close(directory_fd)


def validate_region(
    raw: object,
    proofs: Mapping[str, ScreenshotProof],
    kind: str,
    query: str | None = None,
    identity: str | None = None,
    purposes: set[str] | None = None,
) -> ScreenshotProof:
    """Validate an exact screenshot reference and an in-bounds pixel region."""
    expected_keys = _REGION_KEYS if purposes is None else _REGION_KEYS | {"purpose"}
    if not isinstance(raw, dict) or set(raw) != expected_keys:
        raise ScreenshotEvidenceError("screenshot region is invalid")
    screenshot_id = raw["screenshot_id"]
    bbox = raw["bbox"]
    if not isinstance(screenshot_id, str) or not isinstance(bbox, list) or len(bbox) != 4:
        raise ScreenshotEvidenceError("screenshot region is invalid")
    if any(not isinstance(value, int) or isinstance(value, bool) for value in bbox):
        raise ScreenshotEvidenceError("screenshot region is invalid")
    x, y, width, height = bbox
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ScreenshotEvidenceError("screenshot region is invalid")
    proof = proofs.get(screenshot_id)
    if (
        not isinstance(proof, ScreenshotProof)
        or proof.kind != kind
        or proof.query != query
        or proof.identity != identity
    ):
        raise ScreenshotEvidenceError("screenshot region is invalid")
    if x + width > proof.width or y + height > proof.height:
        raise ScreenshotEvidenceError("screenshot region is invalid")
    if purposes is not None:
        allowed = {"visual", "metrics", "access_state"}
        purpose = raw["purpose"]
        if (
            not isinstance(purposes, set)
            or not purposes <= allowed
            or not isinstance(purpose, str)
            or purpose not in purposes
        ):
            raise ScreenshotEvidenceError("screenshot region is invalid")
    return proof


def _valid_queries(queries: object) -> bool:
    return (
        isinstance(queries, tuple)
        and len(queries) == 3
        and all(isinstance(query, str) and query for query in queries)
        and len(set(queries)) == 3
    )


def _parse_descriptor(
    raw: object,
    platform: Platform,
    queries: tuple[str, str, str],
) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != _DESCRIPTOR_KEYS:
        raise ScreenshotEvidenceError("screenshot registry is invalid")
    screenshot_id = raw["id"]
    kind = raw["kind"]
    relative_path = raw["file"]
    digest = raw["sha256"]
    page_url = raw["page_url"]
    query = raw["query"]
    identity = raw["identity"]
    if not isinstance(screenshot_id, str) or not _IDENTIFIER_RE.fullmatch(screenshot_id):
        raise ScreenshotEvidenceError("screenshot registry is invalid")
    if kind not in {"search", "detail"} or not _safe_relative_path(relative_path):
        raise ScreenshotEvidenceError("screenshot registry is invalid")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ScreenshotEvidenceError("screenshot registry is invalid")
    if not _page_url_matches_platform(page_url, platform):
        raise ScreenshotEvidenceError("screenshot registry is invalid")
    if kind == "search":
        if query not in queries or identity is not None:
            raise ScreenshotEvidenceError("screenshot registry is invalid")
    elif query is not None or not isinstance(identity, str) or not identity:
        raise ScreenshotEvidenceError("screenshot registry is invalid")
    return {
        "id": screenshot_id,
        "kind": kind,
        "file": relative_path,
        "sha256": digest,
        "page_url": page_url,
        "query": query,
        "identity": identity,
    }


def _page_url_matches_platform(page_url: object, platform: Platform) -> bool:
    if not isinstance(page_url, str) or not page_url:
        return False
    try:
        parsed = urlsplit(page_url)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and route_platform(page_url) is platform
        )
    except ValueError:
        return False


def _safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("screenshots/"):
        return False
    basename = value.removeprefix("screenshots/")
    return bool(basename) and "/" not in basename and "\\" not in value and basename not in {".", ".."}


def _open_screenshot_directory(run_dir: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(run_dir / "screenshots", flags)
    except OSError as exc:
        raise ScreenshotEvidenceError("screenshot directory is invalid") from None


def _read_screenshot(directory_fd: int, relative_path: str) -> tuple[int, str, bytes]:
    basename = relative_path.removeprefix("screenshots/")
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        file_fd = os.open(basename, flags, dir_fd=directory_fd)
    except OSError:
        raise ScreenshotEvidenceError("screenshot file is invalid") from None
    try:
        try:
            file_stat = os.fstat(file_fd)
        except OSError:
            raise ScreenshotEvidenceError("screenshot file is invalid") from None
        if not stat.S_ISREG(file_stat.st_mode):
            raise ScreenshotEvidenceError("screenshot file is invalid")
        if file_stat.st_size > _MAX_FILE_BYTES:
            raise ScreenshotEvidenceError("screenshot file is too large")
        digest = hashlib.sha256()
        header = bytearray()
        byte_size = 0
        while True:
            try:
                chunk = os.read(file_fd, _READ_CHUNK_SIZE)
            except OSError:
                raise ScreenshotEvidenceError("screenshot file is invalid") from None
            if not chunk:
                break
            byte_size += len(chunk)
            if byte_size > _MAX_FILE_BYTES:
                raise ScreenshotEvidenceError("screenshot file is too large")
            digest.update(chunk)
            if len(header) < _HEADER_LIMIT:
                header.extend(chunk[:_HEADER_LIMIT - len(header)])
        return byte_size, digest.hexdigest(), bytes(header)
    finally:
        os.close(file_fd)


def _image_metadata(header: bytes) -> tuple[str, int, int]:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_metadata(header)
    if header.startswith(b"\xff\xd8"):
        return _jpeg_metadata(header)
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return _webp_metadata(header)
    raise ScreenshotEvidenceError("screenshot format is invalid")


def _png_metadata(header: bytes) -> tuple[str, int, int]:
    if len(header) < 24 or header[12:16] != b"IHDR":
        raise ScreenshotEvidenceError("screenshot format is invalid")
    if int.from_bytes(header[8:12], "big") != 13:
        raise ScreenshotEvidenceError("screenshot format is invalid")
    return "image/png", int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def _jpeg_metadata(header: bytes) -> tuple[str, int, int]:
    offset = 2
    while offset < len(header):
        if header[offset] != 0xFF:
            raise ScreenshotEvidenceError("screenshot format is invalid")
        while offset < len(header) and header[offset] == 0xFF:
            offset += 1
        if offset >= len(header):
            break
        marker = header[offset]
        offset += 1
        if marker == 0xD9:
            break
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(header):
            break
        segment_length = int.from_bytes(header[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(header):
            raise ScreenshotEvidenceError("screenshot format is invalid")
        if marker in {0xC0, 0xC1, 0xC2}:
            if segment_length < 8:
                raise ScreenshotEvidenceError("screenshot format is invalid")
            height = int.from_bytes(header[offset + 3:offset + 5], "big")
            width = int.from_bytes(header[offset + 5:offset + 7], "big")
            return "image/jpeg", width, height
        offset += segment_length
    raise ScreenshotEvidenceError("screenshot format is invalid")


def _webp_metadata(header: bytes) -> tuple[str, int, int]:
    if len(header) < 16:
        raise ScreenshotEvidenceError("screenshot format is invalid")
    chunk_type = header[12:16]
    if chunk_type == b"VP8X":
        if len(header) < 30 or int.from_bytes(header[16:20], "little") < 10:
            raise ScreenshotEvidenceError("screenshot format is invalid")
        width = int.from_bytes(header[24:27], "little") + 1
        height = int.from_bytes(header[27:30], "little") + 1
    elif chunk_type == b"VP8 ":
        if len(header) < 30 or int.from_bytes(header[16:20], "little") < 10 or header[23:26] != b"\x9d\x01\x2a":
            raise ScreenshotEvidenceError("screenshot format is invalid")
        width = int.from_bytes(header[26:28], "little") & 0x3FFF
        height = int.from_bytes(header[28:30], "little") & 0x3FFF
    elif chunk_type == b"VP8L":
        if len(header) < 25 or int.from_bytes(header[16:20], "little") < 5 or header[20] != 0x2F:
            raise ScreenshotEvidenceError("screenshot format is invalid")
        bits = int.from_bytes(header[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
    else:
        raise ScreenshotEvidenceError("screenshot format is invalid")
    return "image/webp", width, height
