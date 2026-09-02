# Remove Screenshot Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace screenshot-backed browser evidence with a strict DOM-only structured evidence contract.

**Architecture:** `evidence.json` remains the validated handoff between selected Chrome collection and workflow finalization, but its root is exactly `task_record_id`, `platform`, `queries`, and `details`. Workflow and checkpoint replay bind only canonical structured data; the SHEIN extension exports JSON only. Screenshot-era inputs are rejected with no compatibility or migration path.

**Tech Stack:** Python 3 `unittest`, JavaScript ES modules with Node test runner, Chrome Manifest V3, Markdown Skill contracts.

**Spec:** `docs/superpowers/specs/2026-09-02-remove-screenshot-evidence-design.md`

## Global Constraints

- No compatibility, migration, or read-only replay for screenshot-backed evidence or checkpoints.
- Reject `screenshots`, `evidence_ref`, `evidence_refs`, `screenshot_manifest`, and `screenshot_count`; do not silently strip them.
- Keep task/platform/query/identity/order/metric/visual-structure validation and Base write safety unchanged.
- Production changes follow a witnessed RED before GREEN.

---

### Task 1: DOM-only workflow and checkpoint contract

**Files:**
- Modify: `tests/fixtures/shein-evidence.json`
- Modify: `tests/fixtures/mercado-evidence.json`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_checkpoint.py`
- Modify: `scripts/workflow.py`
- Modify: `scripts/checkpoint.py`
- Delete: `tests/test_screenshot_evidence.py`
- Delete: `scripts/screenshot_evidence.py`

**Interfaces:**
- Consumes: prepared task plus direct Ark `queries_resolved` manifest.
- Produces: `_canonical_evidence_payload(run_dir, task, final_queries, raw)` returning canonical structured evidence, candidates, counts, and provenance without screenshot metadata.
- Produces: `CheckpointStore.save_stage(..., "evidence_validated", payload)` whose immutable core rejects screenshot-era keys.

- [ ] **Step 1: Write failing workflow contract tests**

Change fixtures to the four-field root and remove observation/detail screenshot references. Add tests equivalent to:

```python
def test_validate_accepts_dom_only_evidence_without_image_files(self):
    validated = self.validate_fixture("shein-evidence.json")
    self.assertEqual(
        {"task_record_id", "platform", "queries", "details"},
        set(validated["evidence"]),
    )
    self.assertNotIn("screenshot_manifest", validated)
    self.assertNotIn("screenshot_count", validated)

def test_validate_rejects_all_screenshot_era_fields(self):
    for mutation in screenshot_era_mutations():
        with self.subTest(mutation=mutation.name):
            with self.assertRaisesRegex(WorkflowError, "evidence schema is invalid"):
                self.validate_value(mutation.apply(valid_dom_evidence()))
```

- [ ] **Step 2: Run workflow tests and verify RED**

Run: `python3 -m unittest tests.test_workflow -v`

Expected: DOM-only fixtures fail because current workflow requires screenshot roots/references and screenshot files.

- [ ] **Step 3: Write failing checkpoint contract tests**

Update the canonical checkpoint helper to omit screenshot metadata. Assert that a new DOM-only evidence stage is accepted, while evidence containing either removed checkpoint key is rejected. Replace historical compatibility tests with a test that loading or advancing a screenshot-backed checkpoint raises `CheckpointError`.

- [ ] **Step 4: Run checkpoint tests and verify RED**

Run: `python3 -m unittest tests.test_checkpoint -v`

Expected: current immutable-core checks still require `screenshot_manifest` and `screenshot_count`.

- [ ] **Step 5: Implement the DOM-only workflow**

Remove the screenshot validator imports/constants, registry/region validation, screenshot canonicalization, screenshot counts, and historical screenshotless compatibility branches from `scripts/workflow.py`. Make root, observation, and detail `_exact` contracts reject removed keys. Preserve the existing canonical candidate selection, evidence digest, replay, locking, dry-run, and live-write ordering.

- [ ] **Step 6: Implement the DOM-only checkpoint contract**

Remove screenshot keys from `_EVIDENCE_CORE_KEYS` and all screenshot-specific transition logic in `scripts/checkpoint.py`. Keep exact immutable core comparisons so screenshot-era keys fail rather than being ignored.

- [ ] **Step 7: Delete the screenshot validator and run focused GREEN tests**

Delete `scripts/screenshot_evidence.py` and `tests/test_screenshot_evidence.py` with `apply_patch`. Run:

```bash
python3 -m unittest tests.test_workflow tests.test_checkpoint -v
python3 -m py_compile scripts/workflow.py scripts/checkpoint.py
```

Expected: all tests pass and no production import references the deleted module.

- [ ] **Step 8: Commit Task 1**

```bash
git add scripts tests
git commit -m "refactor: use DOM-only product evidence"
```

### Task 2: JSON-only SHEIN collector

**Files:**
- Modify: `tests/js/shein-collector.test.mjs`
- Modify: `tests/test_shein_collector.py`
- Modify: `chrome-extension/shein-evidence-collector/collector.js`
- Modify: `chrome-extension/shein-evidence-collector/collector.html`
- Modify: `chrome-extension/shein-evidence-collector/manifest.json`
- Delete: `chrome-extension/shein-evidence-collector/background.js`

**Interfaces:**
- Consumes: exact task record, platform, and three Ark queries plus visible SHEIN search/detail DOM.
- Produces: one downloaded `evidence.json` matching the four-field workflow schema.

- [ ] **Step 1: Write failing collector tests**

Change Node expectations so session output has exactly four root fields, observations/details lack screenshot references, export downloads exactly one JSON file, and source/manifest contain neither `captureVisibleTab` nor screenshot/background capabilities.

- [ ] **Step 2: Run collector tests and verify RED**

Run:

```bash
node --test tests/js/shein-collector.test.mjs
python3 -m unittest tests.test_shein_collector -v
```

Expected: current collector still requires capture descriptors/regions and exports PNG files.

- [ ] **Step 3: Implement JSON-only collection**

Replace screenshot-framed append operations with DOM-card and DOM-detail append operations. Remove screenshot IDs, files, hashes, bounding boxes, message passing, and binary download state. Export only canonical JSON. Remove the background service worker from the manifest and delete `background.js`; preserve the sole `https://us.shein.com/*` host scope.

- [ ] **Step 4: Run collector GREEN tests**

Run the two commands from Step 2 and verify every test passes.

- [ ] **Step 5: Commit Task 2**

```bash
git add chrome-extension tests
git commit -m "refactor: export structured SHEIN evidence only"
```

### Task 3: Skill, reference, and forward-contract update

**Files:**
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/fixtures/direct-ark-forward-test.md`
- Modify: `tests/fixtures/direct-ark-forward-evaluation.json`
- Modify: `SKILL.md`
- Modify: `references/browser-evidence.md`
- Modify: `references/shein.md`
- Modify as required: `agents/openai.yaml`

**Interfaces:**
- Consumes: the DOM-only code and extension contract from Tasks 1–2.
- Produces: operator instructions and examples that require structured collection and reject screenshot-era input.

- [ ] **Step 1: Write failing Skill contract tests**

Assert that the reference example root is exactly the four fields, observations/details have no screenshot references, the Skill describes DOM-visible structured collection, and no normal-flow instruction requires screenshots, OCR, screenshot hashes, regions, or a screenshot directory. Update the forward fixture so correct behavior proceeds with complete structured evidence and rejects injected screenshot-era keys.

- [ ] **Step 2: Run Skill tests and verify RED**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: current Skill and references still mandate screenshot proof.

- [ ] **Step 3: Update Skill and references**

Remove screenshot-gate text and rewrite `references/browser-evidence.md` around the exact four-field schema. Document JSON-only SHEIN fallback collection, strict rejection of removed fields, unchanged DOM visibility/query/detail rules, and fresh-run handling for old artifacts. Keep `SKILL.md` concise and keep linked references discoverable.

- [ ] **Step 4: Run Skill GREEN tests and validator**

Run:

```bash
python3 -m unittest tests.test_skill_contract -v
python3 /Users/hugo_1/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
```

If the bundled validator cannot import its declared YAML dependency, record that environmental limitation and still run YAML parsing through the repository's existing contract tests.

- [ ] **Step 5: Commit Task 3**

```bash
git add SKILL.md references agents tests
git commit -m "docs: remove screenshot evidence requirement"
```

### Task 4: Repository-wide verification

**Files:**
- Modify only if a verification failure exposes a requirement-specific defect.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: verified branch state with no screenshot evidence implementation or documentation residue.

- [ ] **Step 1: Scan for forbidden residue**

Run:

```bash
rg -n "screenshot|evidence_ref|evidence_refs|screenshots|captureVisibleTab" SKILL.md references agents scripts tests chrome-extension
```

Expected: no normal-flow screenshot evidence code or contract remains; any unavoidable historical design documents are outside this scan.

- [ ] **Step 2: Run full automated verification**

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/shein-collector.test.mjs
python3 -m py_compile scripts/*.py
git diff --check
```

Expected: all tests pass, subject only to a separately identified pre-existing sandbox-only loopback restriction.

- [ ] **Step 3: Verify Git scope**

Run `git status --short` and `git diff --stat HEAD~3..HEAD`; confirm only the approved screenshot-removal scope changed and no real-run data or secrets are tracked.

