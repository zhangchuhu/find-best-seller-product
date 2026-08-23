from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome-extension" / "shein-evidence-collector"
MANIFEST = EXTENSION / "manifest.json"


class SheinCollectorManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_manifest_has_only_required_shein_access(self) -> None:
        self.assertEqual(["https://us.shein.com/*"], self.manifest["host_permissions"])
        self.assertEqual({"activeTab", "downloads"}, set(self.manifest["permissions"]))
        flattened = json.dumps(self.manifest)
        for forbidden in ("cookies", "history", "webRequest", "<all_urls>", "clipboardRead"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, flattened)

    def test_manifest_is_v3_with_exact_shein_content_script(self) -> None:
        self.assertEqual(3, self.manifest["manifest_version"])
        self.assertEqual(
            [{
                "matches": ["https://us.shein.com/*"],
                "js": ["content.js"],
                "run_at": "document_idle",
            }],
            self.manifest["content_scripts"],
        )
        self.assertEqual({"service_worker": "background.js"}, self.manifest["background"])
        self.assertNotIn("update_url", self.manifest)

    def test_extension_html_uses_only_local_static_scripts(self) -> None:
        html = (EXTENSION / "collector.html").read_text(encoding="utf-8")
        self.assertIn('<script src="collector.js"></script>', html)
        self.assertNotIn("<script>", html)
        self.assertNotRegex(html, r'<script[^>]+src=["\'](?:https?:)?//')

    def test_extension_source_has_no_remote_or_session_inspection_primitives(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (EXTENSION / "background.js", EXTENSION / "content.js", EXTENSION / "collector.js")
        )
        for forbidden in (
            "fetch(", "XMLHttpRequest", "document.cookie", "chrome.cookies", "chrome.history",
            "chrome.webRequest", "sessionStorage", "localStorage", "navigator.clipboard",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
