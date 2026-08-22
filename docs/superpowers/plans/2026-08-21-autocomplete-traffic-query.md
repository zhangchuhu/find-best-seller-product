# Platform Autocomplete Traffic-Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Ark-generated long-tail search queries with three color-free and size-free queries selected deterministically from visible marketplace autocomplete suggestions in the explicitly selected Chrome session.

**Architecture:** Ark Vision produces three indexed semantic seeds. A new pure resolver validates ordered autocomplete evidence and selects one verbatim suggestion per seed; the workflow persists a `queries_resolved` stage before accepting marketplace search evidence. Shared low-value-term validation prevents color and size from influencing seeds, final queries, or result visual features.

**Tech Stack:** Python 3 standard library, `unittest`, existing `lark-cli`, Volcengine Ark Chat Completions, selected-Chrome `tab.playwright`, Markdown Codex Skill files.

**Spec:** `docs/superpowers/specs/2026-08-21-autocomplete-traffic-query-design.md`

## Global Constraints

- Final queries come only from visible autocomplete suggestions on Mercado Libre México or SHEIN US; autocomplete order is a traffic-intent signal, not numeric volume.
- Color and size are forbidden in Ark seeds, selected suggestions, final queries, result `视觉特征`, matching, rejection, and ranking.
- Use exactly three indexed seeds and exactly three final queries.
- Use only the explicitly selected Chrome session; no generic web search, private marketplace APIs, cookies/storage, another browser, standalone Playwright, or hidden fallback.
- Preserve 30–50 visible cards per final query, cross-query recurrence, detail thresholds, Base contracts, idempotency, locks, crash consistency, and task-status semantics.
- Use only Python's standard library; do not add dependencies.
- The workspace has no Git metadata. Replace commit steps with plan-scoped filesystem snapshots, review packages, and ledger entries under `.superpowers/sdd/2026-08-21-autocomplete-traffic-query/`.

---

### Task 1: Shared low-value vocabulary and Ark query seeds

**Files:**
- Create: `scripts/query_terms.py`
- Create: `tests/test_query_terms.py`
- Modify: `scripts/ark_vision.py`
- Modify: `tests/test_ark_vision.py`
- Modify: `references/ark-vision.md`

**Interfaces:**
- Produces: `contains_color_or_size(text: str, language: str, source_color: str = "") -> bool` in `scripts.query_terms`.
- Produces: `VisualProfile.query_seeds: tuple[str, str, str]`; removes `VisualProfile.queries` from newly prepared profiles.
- Consumes later: Tasks 2, 4, and 5 use the shared forbidden-term function and `query_seeds` field.

- [ ] **Step 1: Write failing low-value-term tests**

Add table-driven tests proving rejection of `red dress`, `vestido negro`, `plus size dress`, `petite dress`, `tall women dress`, `size 12 dress`, `vestido talla M`, and `XL cocktail dress`; prove acceptance of `3D flower dress`, `long sleeve dress`, `vestido manga larga`, and `A-line mini dress`.

```python
def test_color_and_size_terms_are_detected_without_rejecting_construction():
    rejected = [
        ("red dress", "en-US"), ("vestido negro", "es-MX"),
        ("plus size dress", "en-US"), ("vestido talla M", "es-MX"),
    ]
    for text, language in rejected:
        self.assertTrue(contains_color_or_size(text, language))
    for text, language in [("3D flower dress", "en-US"), ("vestido manga larga", "es-MX")]:
        self.assertFalse(contains_color_or_size(text, language))
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_query_terms tests.test_ark_vision -v`

Expected: import/field failures because `scripts.query_terms` and `query_seeds` do not exist.

- [ ] **Step 3: Implement the shared validator and profile schema**

Create immutable `frozenset` vocabularies for English and Mexican-Spanish color words, explicit size phrases, and compiled size patterns. Normalize with the existing Unicode word policy. Detect source-color vocabulary without treating modifiers such as `solid`, `bright`, or `dark` as colors by themselves.

Update `_PROFILE_FIELDS`, collection limits, serialization, prompt text, and role validation so the model returns `query_seeds`. Enforce indexed roles:

```python
query_seeds = (
    category_seed,
    category_plus_silhouette_or_construction_seed,
    category_plus_style_or_use_scene_seed,
)
```

Every seed must include category vocabulary, match `query_language`, be distinct, and pass `contains_color_or_size(seed, language, profile.color) is False`.

- [ ] **Step 4: Update Ark fixtures and contract examples**

Replace `queries` with short seed examples such as `mini dress`, `puff sleeve mini dress`, and `cocktail dress`. Add prompt assertions that final queries are not requested and that color/size exclusion is explicit.

- [ ] **Step 5: Run focused and dependent tests**

Run: `python3 -m unittest tests.test_query_terms tests.test_ark_vision tests.test_models tests.test_platforms -v`

Expected: all pass with no warnings.

- [ ] **Step 6: Record the task checkpoint**

Write `.superpowers/sdd/2026-08-21-autocomplete-traffic-query/task-1-report.md` with RED output, GREEN output, files changed, and any contract ruling. Append `Task 1: complete` to the plan ledger.

---

### Task 2: Strict autocomplete evidence resolver

**Files:**
- Create: `scripts/autocomplete.py`
- Create: `tests/test_autocomplete.py`
- Create: `tests/fixtures/shein-autocomplete.json`
- Create: `tests/fixtures/mercado-autocomplete.json`
- Create: `references/autocomplete-evidence.md`

**Interfaces:**
- Consumes: `VisualProfile.query_seeds`, `Platform`, and `contains_color_or_size`.
- Produces: immutable `Suggestion(rank: int, text: str)`, `SuggestionBlock(seed: str, suggestions: tuple[Suggestion, ...])`, and `ResolvedQueries(evidence: dict[str, object], queries: tuple[str, str, str])`.
- Produces: `resolve_autocomplete(raw: object, *, record_id: str, platform: Platform, profile: VisualProfile) -> ResolvedQueries`.
- Consumes later: Task 4 persists `ResolvedQueries` into the checkpoint.

- [ ] **Step 1: Write failing schema and selection tests**

Cover exact root/block/item keys, exactly three blocks in seed order, 1–10 suggestions, contiguous ranks, normalized duplicate rejection, record/platform binding, and verbatim selected text.

```python
resolved = resolve_autocomplete(
    fixture("shein-autocomplete.json"),
    record_id="rec_source",
    platform=Platform.SHEIN_US,
    profile=profile(),
)
self.assertEqual(
    ("mini dress", "puff sleeve mini dress", "cocktail dress"),
    resolved.queries,
)
```

Add mutation cases where rank 1 contains a color or size and rank 2 is valid; selection must choose rank 2. If all suggestions are forbidden, wrong-language, category-drifting, role-missing, or duplicate across blocks, resolution must fail without rewriting text.

- [ ] **Step 2: Run resolver tests and verify RED**

Run: `python3 -m unittest tests.test_autocomplete -v`

Expected: import failure because `scripts.autocomplete` does not exist.

- [ ] **Step 3: Implement exact parsing and deterministic resolution**

Use strict JSON-shape helpers, immutable dataclasses, bounded strings, and normalized lexical sets. Iterate suggestions in visible rank order and select the first item that passes category, market language, indexed role, low-value-term, and cross-block uniqueness checks. Return normalized evidence while preserving suggestion strings.

- [ ] **Step 4: Document the evidence contract**

Document the exact JSON shape, the autocomplete-order limitation, Chrome-only collection, rejection rules, and a syntactically valid three-block example. State that the agent never invents, removes words from, or rewrites a suggestion.

- [ ] **Step 5: Run focused tests**

Run: `python3 -m unittest tests.test_autocomplete tests.test_query_terms tests.test_ark_vision -v`

Expected: all pass.

- [ ] **Step 6: Record the task checkpoint**

Write `.superpowers/sdd/2026-08-21-autocomplete-traffic-query/task-2-report.md` and append `Task 2: complete` to the ledger.

---

### Task 3: Add the crash-safe `queries_resolved` checkpoint stage

**Files:**
- Modify: `scripts/checkpoint.py`
- Modify: `tests/test_checkpoint.py`

**Interfaces:**
- Changes: `STAGES = ("prepared", "queries_resolved", "evidence_validated", "finalized")`.
- Changes: newly written checkpoint envelopes use `version: 2`.
- Preserves: `CheckpointStore.save_stage`, `load`, `completed`, descriptor safety, 10 MiB cap, secret scanning, and monotonic transitions.
- Consumes later: Task 4 writes `queries_resolved`; Tasks 4 and 5 read it.

- [ ] **Step 1: Write failing transition tests**

Add tests for prepared → queries_resolved → evidence_validated → finalized, refusal to skip queries_resolved, refusal to regress, exact stage-key prefixes, and replacement only at the current stage. Add compatibility tests proving a version-1 checkpoint is accepted only when it is already finalized with the exact legacy stage set `prepared`, `evidence_validated`, `finalized`; version-1 non-finalized checkpoints are rejected.

- [ ] **Step 2: Run checkpoint tests and verify RED**

Run: `python3 -m unittest tests.test_checkpoint -v`

Expected: failures because `queries_resolved` is not an allowed stage.

- [ ] **Step 3: Implement the stage transition**

Insert `queries_resolved` in `STAGES`, set newly saved envelopes to version 2, and keep ordered-prefix validation for version 2. Add a read-only legacy branch in `_validated_envelope` that accepts only exact version-1 finalized envelopes; `save_stage` must refuse every transition from such an envelope. Do not synthesize autocomplete evidence for legacy data and do not weaken no-follow descriptors, directory identity checks, atomic replacement, fsync, size caps, or secret scanning.

- [ ] **Step 4: Run checkpoint tests and full checkpoint consumers**

Run: `python3 -m unittest tests.test_checkpoint tests.test_workflow -v`

Expected: checkpoint tests pass; workflow tests may fail only where they still construct the old stage sequence, creating the RED boundary for Task 4.

- [ ] **Step 5: Record the task checkpoint**

Write `.superpowers/sdd/2026-08-21-autocomplete-traffic-query/task-3-report.md` and append `Task 3: complete` to the ledger.

---

### Task 4: Workflow query-resolution command and manifest binding

**Files:**
- Modify: `scripts/workflow.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/fixtures/shein-evidence.json`
- Modify: `tests/fixtures/mercado-evidence.json`

**Interfaces:**
- Produces: `resolve_queries(run_dir: Path, input_path: Path) -> str`.
- CLI: `python3 scripts/workflow.py resolve-queries --run-dir <RUN_DIR> --input <AUTOCOMPLETE_JSON>`.
- Prepared payload keys become exactly `task`, `source_image`, `visual_profile`, and `query_seeds`.
- Resolved payload keys become exactly `autocomplete_evidence` and `queries`.
- `manifest.json` after resolution contains prepared fields plus final `queries` without changing either Base.

- [ ] **Step 1: Write failing workflow tests**

Add tests proving prepare outputs seeds but no final queries; validate-evidence before resolution fails with the next-step message; resolve-queries persists exact normalized autocomplete evidence and queries; rerunning identical input is idempotent; semantic changes after resolution fail; dry-run/Base write methods are never invoked.

Add a legacy-finalized test proving `finalize` returns its existing result count without parsing the old Ark `queries` profile or permitting any write; version-1 prepared/evidence checkpoints remain rejected.

- [ ] **Step 2: Run workflow tests and verify RED**

Run: `python3 -m unittest tests.test_workflow -v`

Expected: failures for missing CLI/function and old prepared payload shape.

- [ ] **Step 3: Implement prepared/resolved payload readers**

Add `_queries_resolved_payload(checkpoint, record_id, run_dir)` that first validates `_prepared_payload`, then exact-checks the `queries_resolved` stage and calls `resolve_autocomplete`. Bind record ID, platform, profile seeds, and normalized evidence.

Before calling `_validated_payload`, add a narrow finalized-version-1 read path that exact-checks only the legacy finalized payload (`result_count` and `completed`) and existing `final-results.json`, then returns `Already finalized`; it must not re-enter writes or upgrade the checkpoint.

- [ ] **Step 4: Implement `resolve_queries` and CLI routing**

Acquire the same per-record lock used by evidence validation and finalization. Reject finalized checkpoints and old layouts. Save `queries_resolved`, then atomically rewrite `manifest.json` with `query_seeds` and `queries`. Return a bounded summary naming the record and three final queries without echoing untrusted suggestion lists.

- [ ] **Step 5: Convert existing workflow fixtures**

Provide valid autocomplete fixtures and create checkpoints through resolution before evidence validation. Preserve all previous task, evidence, locking, freshness, crash-window, and Base-write assertions.

- [ ] **Step 6: Run focused workflow tests**

Run: `python3 -m unittest tests.test_workflow tests.test_checkpoint tests.test_autocomplete -v`

Expected: all pass.

- [ ] **Step 7: Record the task checkpoint**

Write `.superpowers/sdd/2026-08-21-autocomplete-traffic-query/task-4-report.md` and append `Task 4: complete` to the ledger.

---

### Task 5: Bind product evidence and result features to resolved, low-value-free queries

**Files:**
- Modify: `scripts/workflow.py`
- Modify: `scripts/candidates.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_candidates.py`

**Interfaces:**
- `validate_evidence` consumes final queries only from `_queries_resolved_payload`.
- Rejected and qualified detail normalization rejects non-null `visual_features` containing color or size terms.
- Candidate ranking remains query hits, organic/ad rank, metrics, match level, and stable identity; it receives no color/size field.

- [ ] **Step 1: Write failing binding and visual-feature tests**

Test that Ark seeds used as evidence queries are rejected, a suggestion not present in resolved state is rejected, and evidence collected before resolution cannot be validated. Add qualified candidate cases where `visual_features` contains `red`, `talla M`, `plus size`, or `petite`; all must fail. Prove `3D flower applique`, `puff sleeves`, and `A-line skirt` remain valid.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_workflow tests.test_candidates -v`

Expected: failures because validation still reads prepared queries and features are not low-value checked.

- [ ] **Step 3: Implement resolved-query binding**

Replace every prepared-query read with the exact `queries_resolved` payload. Include normalized autocomplete evidence in the evidence-validation provenance so a stale or semantically changed resolution cannot be refreshed over newer progress.

- [ ] **Step 4: Implement feature exclusion**

Before constructing `VerifiedCandidate`, validate every feature using `contains_color_or_size`. Do not strip forbidden words or partially rewrite features; fail closed with a bounded error. Confirm no color or size contributes to matching or ranking data.

- [ ] **Step 5: Run workflow, candidate, and Lark integration tests**

Run: `python3 -m unittest tests.test_workflow tests.test_candidates tests.test_lark_base -v`

Expected: all pass.

- [ ] **Step 6: Record the task checkpoint**

Write `.superpowers/sdd/2026-08-21-autocomplete-traffic-query/task-5-report.md` and append `Task 5: complete` to the ledger.

---

### Task 6: Update the Codex Skill and behavioral contracts

**Files:**
- Modify: `SKILL.md`
- Modify: `references/browser-evidence.md`
- Modify: `references/mercado-libre.md`
- Modify: `references/shein.md`
- Modify: `agents/openai.yaml` only if its existing prompt mentions Ark-generated final queries
- Modify: `tests/test_skill_contract.py`
- Create: `tests/fixtures/autocomplete-forward-test.md`

**Interfaces:**
- Skill workflow commands become `prepare`, Chrome autocomplete collection, `resolve-queries`, Chrome product collection, `validate-evidence`, and `finalize`.
- Completion report adds three seed suggestion counts and the three selected verbatim queries.

- [ ] **Step 1: Write failing Skill contract tests**

Require instructions to collect autocomplete before product search, capture 1–10 visible suggestions per seed, use displayed order, select only verbatim suggestions, and forbid color/size in queries and result features. Require explicit language that autocomplete is not numeric volume and that no suggestion may be invented.

- [ ] **Step 2: Run Skill tests and verify RED**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: failures because the installed workflow still says Ark produces final queries.

- [ ] **Step 3: Update Skill and routed references**

Keep `SKILL.md` below 500 words. Link `references/autocomplete-evidence.md` from the workflow step. Preserve Chrome-only, CAPTCHA, threshold, recurrence, partial-result, attachment, Base write, and completion-report rules.

- [ ] **Step 4: Run independent forward behavior test**

Give a fresh subagent only the updated Skill and `tests/fixtures/autocomplete-forward-test.md`. The expected behavior is to refuse a direct colored long-tail query, request/capture platform autocomplete, select three verbatim non-color/non-size suggestions, and omit color/size from visual features. Save the response and evaluator verdict in the task report.

- [ ] **Step 5: Run Skill and workflow contract tests**

Run: `python3 -m unittest tests.test_skill_contract tests.test_autocomplete tests.test_workflow -v`

Expected: all pass.

- [ ] **Step 6: Record the task checkpoint**

Write `.superpowers/sdd/2026-08-21-autocomplete-traffic-query/task-6-report.md` and append `Task 6: complete` to the ledger.

---

### Task 7: Whole-skill validation, installation, and selected-Chrome acceptance

**Files:**
- Modify: `.superpowers/sdd/2026-08-21-autocomplete-traffic-query/progress.md`
- Install after validation: `/Users/hugo_1/.codex/skills/find-best-seller-product/`

**Interfaces:**
- Consumes all previous tasks.
- Produces an installed, validated Skill and a real single-record dry-run acceptance report.

- [ ] **Step 1: Run the full automated suite**

Run: `python3 -m unittest discover -s tests -q`

Expected: all tests pass; run outside the sandbox only for the existing Ark localhost redirect test if required.

- [ ] **Step 2: Compile and validate the source Skill**

Run: `python3 -m compileall -q scripts tests`

Run the bundled `quick_validate.py` against the workspace Skill. Expected: `Skill is valid!`.

- [ ] **Step 3: Perform a real one-record prepare dry-run**

Use one pending task record. Confirm the manifest contains exactly three non-color/non-size seeds and no final queries. Confirm Base status remains `未开始`.

- [ ] **Step 4: Perform selected-Chrome autocomplete acceptance**

In the explicitly selected Chrome session, collect 1–10 visible suggestions per seed from the task's declared platform. Validate that the three selected final queries are verbatim, ordered-platform suggestions without color or size. Run `resolve-queries` and inspect the resolved manifest.

- [ ] **Step 5: Perform product-search dry-run acceptance**

Search the three resolved queries, collect 30–50 cards per query, validate details, and run `finalize --dry-run`. Do not perform a live Base write unless the user separately authorizes that specific acceptance record.

- [ ] **Step 6: Install only runtime files**

Synchronize `SKILL.md`, `agents/`, `references/`, and `scripts/` to `/Users/hugo_1/.codex/skills/find-best-seller-product/`, excluding tests, fixtures, docs, `.work`, reports, `__pycache__`, and `*.pyc`. Verify source/install byte equality and run `quick_validate.py` against the installed directory.

- [ ] **Step 7: Final independent review and ledger closure**

Dispatch one whole-skill reviewer against the spec, plan, source, tests, and acceptance artifacts. Resolve load-bearing findings through one TDD fix round and scoped re-review. Append test counts, validator output, Chrome acceptance counts, installation path, blockers, and `Task 7: complete` to the ledger.
