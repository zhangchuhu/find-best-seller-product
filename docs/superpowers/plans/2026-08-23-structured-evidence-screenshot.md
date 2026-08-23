# Structured Evidence with Screenshot Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require every marketplace search observation and detail outcome to carry tamper-evident screenshot proof while keeping structured fields authoritative.

**Architecture:** A new `scripts/screenshot_evidence.py` module safely opens, identifies, sizes, and hashes screenshot files and validates bounded regions. `scripts/workflow.py` incorporates its normalized screenshot manifest into evidence validation and replays file verification before dry-run or live finalization. A host-restricted Manifest V3 SHEIN collector produces the structured JSON and overlapping viewport screenshots without using the unstable Codex DOM channel.

**Tech Stack:** Python 3 standard library, `unittest`, Chrome Manifest V3 JavaScript, existing checkpoint/workflow modules.

**Spec:** `docs/superpowers/specs/2026-08-23-structured-evidence-screenshot-design.md`

## Global Constraints

- Structured values remain the only inputs to recurrence, filtering, ranking, and Base writes; OCR cannot populate or repair them.
- New evidence root keys are exactly `task_record_id`, `platform`, `screenshots`, `queries`, and `details`.
- Accept PNG, JPEG, and WebP only; derive type, dimensions, and byte size from file bytes.
- Limit each screenshot to 8 MiB, each task to 128 screenshots and 128 MiB, and each dimension to 1–16,384 pixels.
- Screenshot paths are unique POSIX paths exactly under `<run-dir>/screenshots/`; reject absolute paths, backslashes, empty segments, dot segments, symlinks, FIFOs, and non-regular files.
- Revalidate every screenshot before both dry-run and live finalization; any failure occurs before Result Base or task-status mutation.
- Existing `prepared` and `queries_resolved` checkpoints remain resumable; screenshotless validated evidence cannot start a new live write.
- The first collector has only `https://us.shein.com/*` host access and no cookie, history, webRequest, all-URL, clipboard, session-storage, or remote-code capability.
- Preserve all currently uncommitted direct-Ark, visual-structure, and Chrome-retry changes; do not overwrite or fold them into unrelated commits.

---

### Task 1: Descriptor-safe screenshot inspection

**Files:**
- Create: `scripts/screenshot_evidence.py`
- Create: `tests/test_screenshot_evidence.py`

**Interfaces:**
- Consumes: `Path run_dir`, raw screenshot descriptor mappings, `Platform`, and the three exact manifest queries.
- Produces: `ScreenshotProof`, `ScreenshotEvidenceError`, `validate_screenshot_registry(raw, run_dir, platform, queries)`, and `validate_region(raw, proofs, kind, query=None, identity=None, purposes=None)`.

- [ ] **Step 1: Write minimal valid-image and descriptor helpers in the test module**

```python
def png_bytes(width: int = 1000, height: int = 800) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return signature + chunk(b"IHDR", ihdr_data) + chunk(b"IDAT", zlib.compress(b"\x00" + b"\x00\x00\x00\x00" * width)) + chunk(b"IEND", b"")

def write_search_png(run_dir: Path, name: str = "query-1.png") -> tuple[Path, str]:
    target = run_dir / "screenshots" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    content = png_bytes()
    target.write_bytes(content)
    return target, hashlib.sha256(content).hexdigest()
```

- [ ] **Step 2: Add failing happy-path and exact-descriptor tests**

```python
def test_registry_derives_png_metadata_and_preserves_binding(self):
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
```

- [ ] **Step 3: Run the focused test and confirm RED**

Run: `python3 -m unittest tests.test_screenshot_evidence.ScreenshotRegistryTests.test_registry_derives_png_metadata_and_preserves_binding -v`

Expected: FAIL because `scripts.screenshot_evidence` does not exist.

- [ ] **Step 4: Implement immutable types, exact descriptor parsing, no-follow opening, bounded hashing, and PNG inspection**

```python
class ScreenshotEvidenceError(ValueError):
    pass

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
```

Open `<run-dir>/screenshots` with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW`. Require a single basename after the literal `screenshots/` prefix. Open each file relative to that directory with `O_RDONLY | O_NONBLOCK | O_NOFOLLOW`, require `stat.S_ISREG`, reject size above 8 MiB, read through the same descriptor in chunks, and compute SHA-256. Use fixed sanitized `ScreenshotEvidenceError` messages.

- [ ] **Step 5: Add failing JPEG/WebP, unsafe-path, bounded-read, count/total, and duplicate tests**

Add table-driven tests for valid baseline JPEG SOF0, PNG, and WebP VP8X headers plus malformed/truncated variants. Add real symlink and FIFO tests, `../`, absolute paths, backslashes, duplicate IDs, duplicate files, hash mismatch, 129 descriptors, 8 MiB + 1 byte, total 128 MiB + 1 byte, zero dimensions, and 16,385-pixel dimensions. The FIFO test must execute validation in a child process with a two-second timeout and assert a sanitized rejection rather than a hang.

- [ ] **Step 6: Run the expanded test and confirm RED**

Run: `python3 -m unittest tests.test_screenshot_evidence -v`

Expected: FAIL on JPEG/WebP and safety cases not implemented in Step 4.

- [ ] **Step 7: Implement JPEG/WebP dimension parsing and all registry limits**

```python
def _image_metadata(header: bytes) -> tuple[str, int, int]:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_metadata(header)
    if header.startswith(b"\xff\xd8"):
        return _jpeg_metadata(header)
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return _webp_metadata(header)
    raise ScreenshotEvidenceError("screenshot format is invalid")
```

JPEG parsing walks bounded markers to SOF0/SOF1/SOF2; WebP handles VP8, VP8L, and VP8X dimension encodings. Reject malformed segment lengths, missing dimension chunks, non-finite counts, and bytes after the per-file cap without exposing file data.

- [ ] **Step 8: Add region-binding tests, run GREEN, and commit**

Test exact ref keys, integer coordinates excluding booleans, positive width/height, boundary equality, one-pixel overflow, wrong kind, wrong query, wrong identity, and disallowed purpose.

Run: `python3 -m unittest tests.test_screenshot_evidence -v`

Expected: all tests PASS.

```bash
git add scripts/screenshot_evidence.py tests/test_screenshot_evidence.py
git commit -m "feat: validate screenshot evidence files"
```

---

### Task 2: Bind screenshots into product evidence validation

**Files:**
- Modify: `scripts/workflow.py:36-53, 190-205, 660-990`
- Modify: `tests/test_workflow.py:180-225, 260-900`
- Modify: `tests/fixtures/shein-evidence.json`
- Modify: `tests/fixtures/mercado-evidence.json`

**Interfaces:**
- Consumes: Task 1 registry and region validators.
- Produces: screenshot-backed `_canonical_evidence_payload(...)` with canonical `evidence.screenshots`, `screenshot_manifest`, and `screenshot_count`.

- [ ] **Step 1: Upgrade test fixture writers to create deterministic screenshot files**

Create `attach_screenshot_proof(value, run_dir)` in `tests/test_workflow.py`. It writes fixed valid PNGs, adds one search descriptor per query and one detail descriptor per identity, adds observation `evidence_ref` boxes, and adds detail `visual`/`metrics` references. `evidence_fixture` must call it before writing JSON. Add a `write_evidence(root, value, name)` helper and replace direct ad-hoc evidence writes so every pre-existing workflow test starts from valid screenshot-backed evidence before mutating its target field.

```python
def evidence_fixture(root: Path, name: str) -> Path:
    value = load_fixture(name)
    attach_screenshot_proof(value, root / "rec_source")
    return write_evidence(root, value, name)
```

- [ ] **Step 2: Add failing exact-schema and binding tests**

```python
def test_validate_requires_screenshot_root_and_observation_reference(self):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prepare_only(root)
        value = load_fixture("shein-evidence.json")
        path = write_evidence(root, value, "missing-screenshots.json")
        with self.assertRaisesRegex(WorkflowError, "screenshot evidence is invalid"):
            validate_evidence(root / "rec_source", path, clock=frozen_clock())
```

Add failures for unknown root keys, unknown screenshot keys, missing observation ref, a search screenshot bound to another query, detail screenshot bound to another identity/URL, and an out-of-bounds box.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `python3 -m unittest tests.test_workflow.EvidenceValidationTests -v`

Expected: FAIL because the workflow still accepts the old four-key root and seven-key observations.

- [ ] **Step 4: Parse the new root and observation refs before recurrence**

Change the root exact-key check to:

```python
root_value = _exact(
    raw,
    {"task_record_id", "platform", "screenshots", "queries", "details"},
    "evidence",
)
descriptors, screenshot_manifest, proofs = validate_screenshot_registry(
    root_value["screenshots"], Path(run_dir), task.platform, final_queries,
)
```

Extend `_canonical_evidence_payload` with `run_dir: Path`. Parse each observation by separating `evidence_ref` from the seven `Observation` fields, construct the existing immutable model from only those fields, and validate the reference as `kind="search"` with the current query.

- [ ] **Step 5: Add failing detail-purpose matrix tests**

Cover qualified detail missing `visual`, qualified detail missing `metrics`, visual mismatch with only metrics, threshold failure with only visual, identity change without access-state, and each accepted matrix row. Assert that screenshot proof does not alter metrics, match level, or candidate ordering.

- [ ] **Step 6: Run purpose tests and confirm RED**

Run: `python3 -m unittest tests.test_workflow.EvidenceValidationTests.test_qualified_detail_requires_visual_and_metrics_screenshot_purposes -v`

Expected: FAIL because `_detail_candidate` has no `evidence_refs` contract.

- [ ] **Step 7: Validate detail refs and include the manifest in the digest**

Keep the existing detail domain keys separate from `evidence_refs`, then apply this exact purpose matrix:

```python
required_purposes = {
    None: {"visual", "metrics"},
    "visual_structure_mismatch": {"visual"},
    "imagery_ambiguous_or_inaccessible": {"visual"},
    "metric_missing_or_ambiguous": {"metrics"},
    "threshold_failure": {"metrics"},
    "identity_changed": {"access_state"},
    "detail_inaccessible": {"access_state"},
}[reason]
```

Require the purposes to contain the required set, reject unknown purposes, and validate every ref as `kind="detail"` for the current identity. Add `screenshot_manifest` and `screenshot_count` to the checkpoint payload before computing `evidence_digest`.

- [ ] **Step 8: Run focused workflow tests, then commit**

Run: `python3 -m unittest tests.test_screenshot_evidence tests.test_workflow -v`

Expected: all tests PASS.

```bash
git add scripts/workflow.py tests/test_workflow.py tests/fixtures/shein-evidence.json tests/fixtures/mercado-evidence.json
git commit -m "feat: bind screenshots to product evidence"
```

---

### Task 3: Reverify screenshots before finalization and reject legacy live writes

**Files:**
- Modify: `scripts/workflow.py:990-1180, 1360-1485`
- Modify: `scripts/checkpoint.py:45-135`
- Modify: `tests/test_workflow.py:930-1600`
- Modify: `tests/test_checkpoint.py:70-180`

**Interfaces:**
- Consumes: canonical `screenshot_manifest` and evidence descriptors from Task 2.
- Produces: `_validated_payload` replay that recomputes and exactly compares screenshot metadata before any output or Base mutation.

- [ ] **Step 1: Add failing deletion, replacement, and no-side-effect tests**

```python
def test_finalize_rejects_deleted_screenshot_before_output_or_base_calls(self):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prepare_only(root)
        validate_evidence(
            root / "rec_source",
            evidence_fixture(root, "shein-evidence.json"),
            clock=frozen_clock(),
        )
        next((root / "rec_source" / "screenshots").iterdir()).unlink()
        lark = FakeLark()
        with self.assertRaisesRegex(WorkflowError, "screenshot evidence is invalid"):
            finalize(root / "rec_source", lark_client=lark, clock=frozen_clock())
        self.assertEqual([], lark.events)
        self.assertFalse((root / "rec_source" / "final-results.json").exists())
```

Repeat with same-size content replacement, symlink substitution, and changed dimensions. Exercise dry-run and live paths.

- [ ] **Step 2: Run replay tests and confirm RED**

Run: `python3 -m unittest tests.test_workflow.FinalizeTests.test_finalize_rejects_deleted_screenshot_before_output_or_base_calls -v`

Expected: FAIL because finalize trusts the stored evidence digest without reopening screenshots.

- [ ] **Step 3: Recompute screenshot manifest inside `_validated_payload`**

Pass `absolute_run` into `_canonical_evidence_payload` for every validate/replay call. During `_validated_payload`, recompute descriptors and manifest from `evidence["evidence"]`; require exact equality with stored `screenshot_manifest`, `screenshot_count`, canonical evidence, candidates, counts, provenance, and digest before `_write_json(final-results.json, ...)` or Lark client construction.

- [ ] **Step 4: Add failing screenshotless checkpoint tests**

Create a valid pre-feature `evidence_validated` v2 checkpoint by removing `screenshots`, every `evidence_ref(s)`, `screenshot_manifest`, and `screenshot_count` from a validated fixture and recomputing its old digest. Assert both dry-run and live finalization fail with `screenshot-backed evidence must be recollected`, perform no Base call, create no result file, and preserve the checkpoint bytes.

- [ ] **Step 5: Run compatibility tests and confirm RED**

Run: `python3 -m unittest tests.test_workflow.LegacyScreenshotEvidenceTests -v`

Expected: FAIL because current v2 legacy paths still replay screenshotless evidence.

- [ ] **Step 6: Implement fail-closed legacy handling and checkpoint immutability**

Detect the missing screenshot keys before digestless-v2 migration. `prepared` and `queries_resolved` remain unchanged. `evidence_validated` without screenshot fields raises the stable recollection error without writing a lock/checkpoint/result or calling Lark. Historical `finalized` checkpoints remain read-only history but cannot be used to initiate or reconcile a new live write.

Update `CheckpointStore` evidence-stage allowed keys to require and preserve `screenshot_manifest` and `screenshot_count`; refreshes may change only existing monotonic timestamp/progress fields and never replace screenshot data.

- [ ] **Step 7: Run workflow/checkpoint suites and commit**

Run: `python3 -m unittest tests.test_screenshot_evidence tests.test_workflow tests.test_checkpoint -v`

Expected: all tests PASS.

```bash
git add scripts/workflow.py scripts/checkpoint.py tests/test_workflow.py tests/test_checkpoint.py
git commit -m "fix: reverify screenshot proof before finalize"
```

---

### Task 4: Build the host-restricted SHEIN collector

**Files:**
- Create: `chrome-extension/shein-evidence-collector/manifest.json`
- Create: `chrome-extension/shein-evidence-collector/background.js`
- Create: `chrome-extension/shein-evidence-collector/content.js`
- Create: `chrome-extension/shein-evidence-collector/collector.html`
- Create: `chrome-extension/shein-evidence-collector/collector.js`
- Create: `tests/test_shein_collector.py`
- Create: `tests/js/shein-collector.test.mjs`

**Interfaces:**
- Consumes: active `https://us.shein.com/*` search/detail page, task record ID, platform, and exact three Ark queries entered by the operator.
- Produces: downloaded `evidence.json` plus files under `screenshots/`, with schema accepted by Tasks 1–3.

- [ ] **Step 1: Add failing manifest security contract tests**

```python
def test_manifest_has_only_required_shein_access(self):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    self.assertEqual(["https://us.shein.com/*"], manifest["host_permissions"])
    self.assertEqual({"activeTab", "downloads"}, set(manifest["permissions"]))
    flattened = json.dumps(manifest)
    for forbidden in ("cookies", "history", "webRequest", "<all_urls>", "clipboardRead"):
        self.assertNotIn(forbidden, flattened)
```

Also assert Manifest V3, local static scripts only, no `update_url`, no external script sources, and exact SHEIN content-script matches.

- [ ] **Step 2: Run manifest test and confirm RED**

Run: `python3 -m unittest tests.test_shein_collector.SheinCollectorManifestTests -v`

Expected: FAIL because the extension directory does not exist.

- [ ] **Step 3: Add the minimal manifest and static collector shell**

```json
{
  "manifest_version": 3,
  "name": "SHEIN Local Evidence Collector",
  "version": "0.1.0",
  "permissions": ["activeTab", "downloads"],
  "host_permissions": ["https://us.shein.com/*"],
  "background": {"service_worker": "background.js"},
  "action": {"default_title": "Collect SHEIN evidence"},
  "content_scripts": [{
    "matches": ["https://us.shein.com/*"],
    "js": ["content.js"],
    "run_at": "document_idle"
  }]
}
```

The action opens `collector.html`. No inline JavaScript is allowed by extension CSP.

- [ ] **Step 4: Add failing pure-JavaScript extraction tests**

Export pure functions from `content.js` when `globalThis.__SHEIN_COLLECTOR_TEST__` is set: `canonicalProductId(url)`, `normalizeBox(rect, viewport)`, `classifyVisibleAd(card)`, and `mergeFirstVisible(cards, prior)`. The Node test supplies synthetic card objects and asserts stable first-visible rank, visible `Sponsored` handling, ambiguity rejection, ID/URL agreement, box clipping, and no color/size rewriting.

Run: `node --test tests/js/shein-collector.test.mjs`

Expected: FAIL because extraction functions are absent.

- [ ] **Step 5: Implement visible extraction and screenshot messaging**

`content.js` reads only rendered elements, requires a visible product link/title/image and unambiguous visible ad state, records `getBoundingClientRect()` boxes, and never fetches. `background.js` calls `chrome.tabs.captureVisibleTab` only after the action click, hashes the resulting bytes with `crypto.subtle.digest("SHA-256", bytes)`, and returns the screenshot descriptor. `collector.js` coordinates 20% overlapping scroll steps by sending a scroll command to `content.js`; the content script scrolls the SHEIN page, preserves first visible order, and stops when the chosen limit between 30 and 50 is reached.

```javascript
const overlap = Math.floor(window.innerHeight * 0.20);
const step = Math.max(1, window.innerHeight - overlap);
window.scrollBy({top: step, left: 0, behavior: "instant"});
```

- [ ] **Step 6: Add failing package/schema tests**

The Node test simulates three query sessions and detail captures, then asserts exact root keys, three byte-for-byte queries, 30–50 contiguous ranks, screenshot ID/file uniqueness, search/query binding, detail identity binding, required purposes, and lowercase SHA-256. Assert the collector stops and emits a visible error on CAPTCHA/login/region-wall markers or ambiguous ads.

- [ ] **Step 7: Implement deterministic export and run GREEN**

Use `chrome.downloads.download` with filenames under `find-best-seller-product/<record-id>/screenshots/` and one sibling `evidence.json`. Sanitize `record-id` to the existing record grammar, reject collisions within a session, and serialize JSON with stable key insertion order. The operator copies the downloaded task directory into the prepared run directory before validation.

Run:

```bash
node --test tests/js/shein-collector.test.mjs
python3 -m unittest tests.test_shein_collector -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit the collector**

```bash
git add chrome-extension/shein-evidence-collector tests/test_shein_collector.py tests/js/shein-collector.test.mjs
git commit -m "feat: add restricted SHEIN evidence collector"
```

---

### Task 5: Update the Skill contract and operator instructions

**Files:**
- Modify: `SKILL.md`
- Modify: `references/browser-evidence.md`
- Modify: `references/shein.md`
- Modify: `agents/openai.yaml`
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/fixtures/direct-ark-forward-test.md`
- Modify: `tests/fixtures/direct-ark-forward-evaluation.json`

**Interfaces:**
- Consumes: implemented validator and collector behavior from Tasks 1–4.
- Produces: concise Skill routing to screenshot-backed evidence and an installation/collection contract that matches executable behavior.

- [ ] **Step 1: Add failing behavioral contract assertions**

Require the Skill/reference corpus to state that structured fields are authoritative, every card/detail binds to screenshots, missing/hash-invalid proof rejects, finalize revalidates, OCR cannot populate fields, collector host permission is SHEIN-only, and screenshot failures cause zero Base/status writes. Require the forward fixture to choose the local collector after bounded Chrome DOM failure while preserving exact Ark seeds and dry-run.

- [ ] **Step 2: Run contract tests and confirm RED**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL because current documentation still describes DOM-only collection and the old four-key evidence root.

- [ ] **Step 3: Update progressive-disclosure documentation**

Keep `SKILL.md` short: route normal data collection to `references/browser-evidence.md`, name the bundled collector directory, and state the hard screenshot gate. Put exact JSON, purpose matrix, file limits, installation steps, and failure handling in the browser reference. Update SHEIN reference with direct exact-query navigation and collector restrictions. Preserve existing Ark, filtering, category-visual, retry, CAPTCHA, and Base boundaries.

- [ ] **Step 4: Replace the compact schema example**

The example must be syntactically valid and contain all new exact objects, while clearly remaining abbreviated/non-submittable because it has fewer than 30 observations. Use no colors or sizes in queries or visual features.

- [ ] **Step 5: Update the blind forward fixture and bound hashes**

Add a realistic DOM-timeout stimulus without leaking expected response labels. Recompute every SHA-256 stored in `direct-ark-forward-evaluation.json` from the final Skill/reference files and keep the existing recomputation test green.

- [ ] **Step 6: Run contract/workflow tests and commit**

Run: `python3 -m unittest tests.test_skill_contract tests.test_workflow tests.test_screenshot_evidence tests.test_shein_collector -v`

Expected: all tests PASS.

```bash
git add SKILL.md references/browser-evidence.md references/shein.md agents/openai.yaml tests/test_skill_contract.py tests/fixtures/direct-ark-forward-test.md tests/fixtures/direct-ark-forward-evaluation.json
git commit -m "docs: require screenshot-backed product evidence"
```

---

### Task 6: Full verification and real dry-run acceptance

**Files:**
- Modify only if a failing verification exposes a requirement defect in files already owned by Tasks 1–5.
- Create: `.superpowers/sdd/2026-08-23-structured-evidence-screenshot/verification-report.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: reproducible verification evidence and one no-write real-task acceptance report.

- [ ] **Step 1: Run static and focused verification**

```bash
python3 -m py_compile scripts/*.py tests/*.py
python3 -m unittest tests.test_screenshot_evidence tests.test_workflow tests.test_checkpoint tests.test_shein_collector tests.test_skill_contract -v
node --test tests/js/shein-collector.test.mjs
```

Expected: all commands exit 0 with no warnings or unexpected skips.

- [ ] **Step 2: Run the full suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests PASS. If the known sandbox-only Ark loopback bind is denied, rerun only that existing test with approved loopback permission and record both outputs; do not weaken the test.

- [ ] **Step 3: Validate extension packaging manually**

Load `chrome-extension/shein-evidence-collector` through `chrome://extensions` → Developer mode → Load unpacked. Confirm Chrome reports site access only for `https://us.shein.com/*`, then reload the SHEIN US page. Do not approve broader host access.

- [ ] **Step 4: Execute the selected record as a dry-run**

Use record `rec27Zl4crfV3C` and its existing `queries_resolved` checkpoint. Collect exactly the stored Ark seeds, copy the downloaded package into the run directory, then run:

```bash
python3 scripts/workflow.py validate-evidence \
  --run-dir .work-real-visual-20260823/rec27Zl4crfV3C \
  --input .work-real-visual-20260823/rec27Zl4crfV3C/evidence.json
python3 scripts/workflow.py finalize \
  --run-dir .work-real-visual-20260823/rec27Zl4crfV3C \
  --dry-run
```

Expected: validation reports three 30–50-card query blocks and verified screenshot count; dry-run creates only local `final-results.json`; task status remains `未开始`; Result Base receives zero writes.

- [ ] **Step 5: Record verification and inspect the final diff**

Write exact command outputs, test counts, real record/SKU/platform, three Ark seeds, per-query counts, screenshot count, recurring count, qualifying count, dry-run result count, blockers, and Base/status write confirmation into the report. Run:

```bash
git diff --check
git status --short
git log --oneline -8
```

Expected: no whitespace errors; only intentional files remain modified; no secret, model response, image Base64, cookie, or session data appears in the diff.

- [ ] **Step 6: Commit the verification report**

```bash
git add .superpowers/sdd/2026-08-23-structured-evidence-screenshot/verification-report.md
git commit -m "test: verify screenshot-backed evidence workflow"
```
