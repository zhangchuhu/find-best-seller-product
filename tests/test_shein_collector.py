from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.workflow import validate_evidence
from tests.test_workflow import prepare_only


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

    def test_manifest_is_v3_with_popup_and_no_background_worker(self) -> None:
        self.assertEqual(3, self.manifest["manifest_version"])
        self.assertEqual("collector.html", self.manifest["action"]["default_popup"])
        self.assertNotIn("background", self.manifest)
        self.assertFalse((EXTENSION / "background.js").exists())
        self.assertEqual(
            [{
                "matches": ["https://us.shein.com/*"],
                "js": ["content.js"],
                "run_at": "document_idle",
            }],
            self.manifest["content_scripts"],
        )

    def test_extension_source_is_local_dom_only_and_has_no_screenshot_path(self) -> None:
        html = (EXTENSION / "collector.html").read_text(encoding="utf-8")
        self.assertIn('<script src="collector.js"></script>', html)
        self.assertNotRegex(html, r'<script[^>]+src=["\'](?:https?:)?//')
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (EXTENSION / "content.js", EXTENSION / "collector.js", EXTENSION / "collector.html")
        )
        for forbidden in (
            "fetch(", "XMLHttpRequest", "document.cookie", "chrome.cookies", "chrome.history",
            "chrome.webRequest", "sessionStorage", "localStorage", "navigator.clipboard",
            "captureVisibleTab", "SHEIN_CAPTURE_VISIBLE", "screenshot", "evidence_ref", "evidence_refs",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class SheinCollectorWorkflowIntegrationTests(unittest.TestCase):
    def test_collector_generated_json_passes_real_workflow_validator(self) -> None:
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
  const cards = Array.from({length: 30}, (_, index) => {
    const productId = index < 2 ? String(100001 + index) : String(300000 + queryIndex * 100 + index);
    return {query, rank: index + 1, is_ad: false, title: `Dress ${productId}`,
      url: `https://us.shein.com/dress-p-${productId}.html`, product_id: productId,
      thumbnail_url: null, bbox: [0, index * 10, 100, 10]};
  });
  api.addSearchObservations(session, {query, cards});
}
const detailUrl = "https://us.shein.com/dress-p-100001.html";
api.addDetailOutcome(session, {identity: "shein-us:100001", status: "qualified", reason: null, detail_url: detailUrl,
  product_id: "100001", title: "Puff Sleeve Mini Dress", category: "mini dress", sold_display: null,
  reviews_display: "500", rating_display: "4.8", match_level: "同款", visual_features: ["puff sleeves", "bow"]});
process.stdout.write(api.serializeEvidence(session));
'''
        completed = subprocess.run(
            ["node", "-e", harness, str(EXTENSION / "collector.js")],
            check=True, text=True, capture_output=True,
        )
        evidence = json.loads(completed.stdout)
        self.assertEqual({"task_record_id", "platform", "queries", "details"}, set(evidence))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_only(root, limit=1)
            run_dir = root / "rec_source"
            evidence_path = root / "collector-evidence.json"
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")

            summary = validate_evidence(run_dir, evidence_path)

        self.assertIn("2 recurring", summary)
        self.assertIn("1 details", summary)


if __name__ == "__main__":
    unittest.main()
