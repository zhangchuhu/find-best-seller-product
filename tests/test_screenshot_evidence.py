from __future__ import annotations

import hashlib
import multiprocessing
import os
from pathlib import Path
import queue
import struct
import tempfile
import unittest
import zlib

from scripts.platforms import Platform
from scripts.screenshot_evidence import (
    ScreenshotEvidenceError,
    ScreenshotProof,
    validate_region,
    validate_screenshot_registry,
)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def png_bytes(width: int = 1000, height: int = 800) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        signature
        + png_chunk(b"IHDR", ihdr_data)
        + png_chunk(b"IDAT", zlib.compress(b"\x00" + b"\x00\x00\x00\x00" * width))
        + png_chunk(b"IEND", b"")
    )


def png_with_byte_size(byte_size: int) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    iend = png_chunk(b"IEND", b"")
    idat_data = b"\x00" * (byte_size - len(signature) - len(ihdr) - len(iend) - 12)
    return signature + ihdr + png_chunk(b"IDAT", idat_data) + iend


def write_search_png(run_dir: Path, name: str = "query-1.png") -> tuple[Path, str]:
    target = run_dir / "screenshots" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    content = png_bytes()
    target.write_bytes(content)
    return target, hashlib.sha256(content).hexdigest()


def jpeg_bytes(width: int = 1000, height: int = 800) -> bytes:
    components = b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    length = 8 + len(components)
    return b"\xff\xd8\xff\xc0" + struct.pack(">H", length) + b"\x08" + struct.pack(">HH", height, width) + b"\x03" + components + b"\xff\xd9"


def webp_vp8x_bytes(width: int = 1000, height: int = 800) -> bytes:
    payload = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + (len(payload) + 12).to_bytes(4, "little") + b"WEBPVP8X" + len(payload).to_bytes(4, "little") + payload


def write_image(run_dir: Path, name: str, content: bytes) -> tuple[Path, str]:
    target = run_dir / "screenshots" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target, hashlib.sha256(content).hexdigest()


def search_descriptor(name: str, digest: str, *, screenshot_id: str = "query-1-frame-001") -> dict[str, object]:
    return {
        "id": screenshot_id,
        "kind": "search",
        "file": f"screenshots/{name}",
        "sha256": digest,
        "page_url": "https://us.shein.com/pdsearch/mini%20dress/",
        "query": "mini dress",
        "identity": None,
    }


def detail_descriptor(name: str, digest: str, *, screenshot_id: str = "detail-1-frame-001") -> dict[str, object]:
    return {
        "id": screenshot_id,
        "kind": "detail",
        "file": f"screenshots/{name}",
        "sha256": digest,
        "page_url": "https://us.shein.com/product-p-12345.html",
        "query": None,
        "identity": "shein-us:12345",
    }


def validate_fifo_in_child(run_dir: str, raw: list[dict[str, object]], result: multiprocessing.Queue) -> None:
    try:
        validate_screenshot_registry(
            raw, Path(run_dir), Platform.SHEIN_US,
            ("mini dress", "puff sleeve mini dress", "cocktail dress"),
        )
    except ScreenshotEvidenceError as exc:
        result.put(str(exc))
    else:
        result.put("accepted")


class ScreenshotRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_registry_derives_png_metadata_and_preserves_binding(self) -> None:
        target, digest = write_search_png(self.run_dir)
        raw = [{
            "id": "query-1-frame-001", "kind": "search",
            "file": "screenshots/query-1.png", "sha256": digest,
            "page_url": "https://us.shein.com/pdsearch/mini%20dress/",
            "query": "mini dress", "identity": None,
        }]
        descriptors, manifest, proofs = validate_screenshot_registry(
            raw, self.run_dir, Platform.SHEIN_US,
            ("mini dress", "puff sleeve mini dress", "cocktail dress"),
        )
        self.assertEqual("image/png", manifest[0]["media_type"])
        self.assertEqual([1000, 800], [manifest[0]["width"], manifest[0]["height"]])
        self.assertEqual(target.stat().st_size, manifest[0]["byte_size"])
        self.assertEqual("query-1-frame-001", descriptors[0]["id"])
        self.assertIn("query-1-frame-001", proofs)

    def test_registry_derives_supported_format_metadata(self) -> None:
        cases = (
            ("png", png_bytes(321, 654), "image/png"),
            ("jpeg", jpeg_bytes(321, 654), "image/jpeg"),
            ("webp", webp_vp8x_bytes(321, 654), "image/webp"),
        )
        for name, content, media_type in cases:
            with self.subTest(name=name):
                _, digest = write_image(self.run_dir, f"{name}.img", content)
                _, manifest, _ = validate_screenshot_registry(
                    [search_descriptor(f"{name}.img", digest, screenshot_id=f"{name}-1")],
                    self.run_dir, Platform.SHEIN_US,
                    ("mini dress", "puff sleeve mini dress", "cocktail dress"),
                )
                self.assertEqual(media_type, manifest[0]["media_type"])
                self.assertEqual([321, 654], [manifest[0]["width"], manifest[0]["height"]])

    def test_registry_rejects_malformed_or_truncated_image_headers(self) -> None:
        dimensions = struct.pack(">II", 321, 654)
        incomplete_ihdr = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + dimensions
        ihdr_without_image = b"\x89PNG\r\n\x1a\n" + png_chunk(
            b"IHDR", dimensions + b"\x08\x06\x00\x00\x00",
        )
        malformed_sof = b"\xff\xd8\xff\xc0\x00\x08\x08" + struct.pack(">HH", 654, 321) + b"\x01\xff\xd9"
        valid_webp = webp_vp8x_bytes(321, 654)
        declared_overlong_chunk = valid_webp[:16] + (11).to_bytes(4, "little") + valid_webp[20:]
        inconsistent_riff_size = valid_webp[:4] + (len(valid_webp) - 7).to_bytes(4, "little") + valid_webp[8:]
        cases = (
            ("png-incomplete-ihdr", incomplete_ihdr),
            ("png-without-image", ihdr_without_image),
            ("jpeg-missing-component-specification", malformed_sof),
            ("webp-overlong-chunk", declared_overlong_chunk),
            ("webp-inconsistent-riff-size", inconsistent_riff_size),
        )
        for name, content in cases:
            with self.subTest(name=name):
                _, digest = write_image(self.run_dir, f"bad-{name}.img", content)
                with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot format is invalid"):
                    validate_screenshot_registry(
                        [search_descriptor(f"bad-{name}.img", digest, screenshot_id=f"bad-{name}")],
                        self.run_dir, Platform.SHEIN_US,
                        ("mini dress", "puff sleeve mini dress", "cocktail dress"),
                    )

    def test_registry_rejects_unsafe_or_nonunique_paths(self) -> None:
        _, digest = write_search_png(self.run_dir)
        invalid_paths = ("../query-1.png", "/tmp/query-1.png", "screenshots/a/b.png", "screenshots\\query-1.png")
        for path in invalid_paths:
            with self.subTest(path=path):
                descriptor = search_descriptor("query-1.png", digest)
                descriptor["file"] = path
                with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot registry is invalid"):
                    validate_screenshot_registry(
                        [descriptor], self.run_dir, Platform.SHEIN_US,
                        ("mini dress", "puff sleeve mini dress", "cocktail dress"),
                    )
        first = search_descriptor("query-1.png", digest)
        duplicate_id = search_descriptor("query-1.png", digest)
        duplicate_file = search_descriptor("query-1.png", digest, screenshot_id="query-1-frame-002")
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot registry is invalid"):
            validate_screenshot_registry(
                [first, duplicate_id], self.run_dir, Platform.SHEIN_US,
                ("mini dress", "puff sleeve mini dress", "cocktail dress"),
            )
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot registry is invalid"):
            validate_screenshot_registry(
                [first, duplicate_file], self.run_dir, Platform.SHEIN_US,
                ("mini dress", "puff sleeve mini dress", "cocktail dress"),
            )

    def test_registry_rejects_symlink_without_following_it(self) -> None:
        outside = self.run_dir / "outside.png"
        outside.write_bytes(png_bytes())
        screenshot_directory = self.run_dir / "screenshots"
        screenshot_directory.mkdir()
        os.symlink(outside, screenshot_directory / "linked.png")
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot file is invalid"):
            validate_screenshot_registry(
                [search_descriptor("linked.png", digest)], self.run_dir, Platform.SHEIN_US,
                ("mini dress", "puff sleeve mini dress", "cocktail dress"),
            )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_registry_rejects_fifo_without_hanging(self) -> None:
        screenshot_directory = self.run_dir / "screenshots"
        screenshot_directory.mkdir()
        os.mkfifo(screenshot_directory / "pending.png")
        result: multiprocessing.Queue = multiprocessing.Queue()
        process = multiprocessing.Process(
            target=validate_fifo_in_child,
            args=(str(self.run_dir), [search_descriptor("pending.png", "0" * 64)], result),
        )
        process.start()
        process.join(2)
        self.assertFalse(process.is_alive(), "FIFO validation hung")
        if process.is_alive():
            process.terminate()
            process.join()
        try:
            message = result.get(timeout=0.5)
        except queue.Empty as exc:
            self.fail(f"FIFO validation returned no sanitized result: {exc}")
        self.assertEqual("screenshot file is invalid", message)

    def test_registry_enforces_hash_count_file_and_total_byte_limits(self) -> None:
        _, digest = write_search_png(self.run_dir)
        descriptor = search_descriptor("query-1.png", digest)
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot digest does not match"):
            validate_screenshot_registry(
                [search_descriptor("query-1.png", "0" * 64)], self.run_dir, Platform.SHEIN_US,
                ("mini dress", "puff sleeve mini dress", "cocktail dress"),
            )
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot registry is invalid"):
            validate_screenshot_registry(
                [descriptor] * 129, self.run_dir, Platform.SHEIN_US,
                ("mini dress", "puff sleeve mini dress", "cocktail dress"),
            )
        oversized = png_bytes() + b"\x00" * (8 * 1024 * 1024)
        _, oversized_digest = write_image(self.run_dir, "oversized.png", oversized)
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot file is too large"):
            validate_screenshot_registry(
                [search_descriptor("oversized.png", oversized_digest)], self.run_dir, Platform.SHEIN_US,
                ("mini dress", "puff sleeve mini dress", "cocktail dress"),
            )
        chunk = png_with_byte_size(8 * 1024 * 1024)
        descriptors = []
        for index in range(16):
            name = f"total-{index}.png"
            _, chunk_digest = write_image(self.run_dir, name, chunk)
            descriptors.append(search_descriptor(name, chunk_digest, screenshot_id=f"total-{index}"))
        _, one_byte_digest = write_image(self.run_dir, "total-overflow.png", b"x")
        descriptors.append(search_descriptor("total-overflow.png", one_byte_digest, screenshot_id="total-overflow"))
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot total is too large"):
            validate_screenshot_registry(
                descriptors, self.run_dir, Platform.SHEIN_US,
                ("mini dress", "puff sleeve mini dress", "cocktail dress"),
            )

    def test_registry_rejects_invalid_dimensions(self) -> None:
        cases = (("zero", png_bytes(0, 1)), ("too-wide", png_bytes(16_385, 1)))
        for name, content in cases:
            with self.subTest(name=name):
                _, digest = write_image(self.run_dir, f"{name}.png", content)
                with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot dimensions are invalid"):
                    validate_screenshot_registry(
                        [search_descriptor(f"{name}.png", digest, screenshot_id=name)],
                        self.run_dir, Platform.SHEIN_US,
                        ("mini dress", "puff sleeve mini dress", "cocktail dress"),
                    )


class ScreenshotRegionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proofs = {
            "search-frame": ScreenshotProof(
                "search-frame", "search", "screenshots/search.png", "0" * 64,
                "https://us.shein.com/pdsearch/mini%20dress/", "mini dress", None,
                "image/png", 1000, 800, 12,
            ),
            "detail-frame": ScreenshotProof(
                "detail-frame", "detail", "screenshots/detail.png", "1" * 64,
                "https://us.shein.com/product-p-12345.html", None, "shein-us:12345",
                "image/png", 1000, 800, 12,
            ),
        }

    def test_region_accepts_exact_search_reference_at_boundary(self) -> None:
        proof = validate_region(
            {"screenshot_id": "search-frame", "bbox": [0, 0, 1000, 800]},
            self.proofs, "search", query="mini dress",
        )
        self.assertEqual("search-frame", proof.screenshot_id)

    def test_region_rejects_malformed_coordinates_and_overflow(self) -> None:
        invalid = (
            {"screenshot_id": "search-frame", "bbox": [0, 0, 1001, 800]},
            {"screenshot_id": "search-frame", "bbox": [0, 0, True, 1]},
            {"screenshot_id": "search-frame", "bbox": [0, 0, 1.0, 1]},
            {"screenshot_id": "search-frame", "bbox": [0, 0, 0, 1]},
            {"screenshot_id": "search-frame", "bbox": [0, 0, 1, 1], "extra": 1},
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot region is invalid"):
                    validate_region(raw, self.proofs, "search", query="mini dress")

    def test_region_rejects_wrong_binding_and_disallowed_purpose(self) -> None:
        reference = {"screenshot_id": "detail-frame", "bbox": [1, 1, 1, 1], "purpose": "visual"}
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot region is invalid"):
            validate_region(reference, self.proofs, "search", query="mini dress")
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot region is invalid"):
            validate_region(reference, self.proofs, "detail", identity="wrong")
        self.assertEqual(
            "detail-frame",
            validate_region(reference, self.proofs, "detail", identity="shein-us:12345", purposes={"visual"}).screenshot_id,
        )
        with self.assertRaisesRegex(ScreenshotEvidenceError, "screenshot region is invalid"):
            validate_region(reference, self.proofs, "detail", identity="shein-us:12345", purposes={"metrics"})
