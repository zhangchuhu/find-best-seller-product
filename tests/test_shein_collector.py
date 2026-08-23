from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.workflow import validate_evidence
from tests.test_workflow import SCREENSHOT_PNG, prepare_only


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


class SheinCollectorWorkflowIntegrationTests(unittest.TestCase):
    def test_collector_generated_package_passes_real_workflow_validator(self) -> None:
        digest = hashlib.sha256(SCREENSHOT_PNG).hexdigest()
        harness = r'''
const fs = require("fs");
const vm = require("vm");
const exportsForTest = {};
const context = vm.createContext({URL, console, globalThis: {__SHEIN_COLLECTOR_TEST__: exportsForTest}});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const api = exportsForTest;
const queries = ["mini dress", "puff sleeve mini dress", "cocktail dress"];
const session = api.createSession({taskRecordId: "rec_source", platform: "shein-us", queries, cardLimit: 30, resultLimit: 1});
for (let queryIndex = 0; queryIndex < queries.length; queryIndex += 1) {
  const query = queries[queryIndex];
  const id = `search-${queryIndex + 1}`;
  const page = `https://us.shein.com/pdsearch/${encodeURIComponent(query)}/`;
  const cards = Array.from({length: 30}, (_, index) => {
    const productId = index < 2 ? String(100001 + index) : String(300000 + queryIndex * 100 + index);
    return {query, rank: index + 1, is_ad: false, title: `Dress ${productId}`,
      url: `https://us.shein.com/dress-p-${productId}.html`, product_id: productId,
      thumbnail_url: null, bbox: [0, index * 10, 100, 10]};
  });
  api.addSearchFrame(session, {query, screenshot: {id, file: `screenshots/${id}.png`, sha256: process.argv[2], page_url: page}, cards});
}
const detailUrl = "https://us.shein.com/dress-p-100001.html";
api.addDetailCapture(session, {screenshot: {id: "detail-100001", file: "screenshots/detail-100001.png", sha256: process.argv[2], page_url: detailUrl},
  detail: {identity: "shein-us:100001", status: "qualified", reason: null, detail_url: detailUrl,
    product_id: "100001", title: "Puff Sleeve Mini Dress", category: "mini dress", sold_display: null,
    reviews_display: "500", rating_display: "4.8", match_level: "同款", visual_features: ["puff sleeves", "bow"]},
  regions: [{purpose: "visual", bbox: [0, 0, 500, 400]}, {purpose: "metrics", bbox: [500, 0, 500, 400]}]});
process.stdout.write(api.serializeEvidence(session));
'''
        completed = subprocess.run(
            ["node", "-e", harness, str(EXTENSION / "collector.js"), digest],
            check=True, text=True, capture_output=True,
        )
        evidence = json.loads(completed.stdout)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_only(root, limit=1)
            run_dir = root / "rec_source"
            screenshot_dir = run_dir / "screenshots"
            screenshot_dir.mkdir()
            for descriptor in evidence["screenshots"]:
                (run_dir / descriptor["file"]).write_bytes(SCREENSHOT_PNG)
            evidence_path = root / "collector-evidence.json"
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")

            summary = validate_evidence(run_dir, evidence_path)

        self.assertIn("2 recurring", summary)
        self.assertIn("1 details", summary)


if __name__ == "__main__":
    unittest.main()
