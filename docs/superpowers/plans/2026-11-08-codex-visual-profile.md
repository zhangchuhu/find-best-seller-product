# Codex Visual Profile Implementation Plan

> **For agentic workers:** Use `executing-plans` or, if explicitly authorized, `subagent-driven-development`. Track steps with checkboxes.

**Goal:** Replace Ark with Codex image inspection and validated profile import, with closed taxonomy and category-only visual filtering.

**Architecture:** Prepare downloads the image and persists a task snapshot; Codex inspects it and supplies a JSON profile to `import-profile`. Deterministic Python validates the profile, binds queries and candidate category evidence, and preserves the existing Lark write and checkpoint safety boundaries.

**Tech Stack:** Python standard library, unittest, Chrome DOM evidence, lark-cli.

**Spec:** `docs/superpowers/specs/2026-11-08-codex-visual-profile-design.md` (all taxonomy tables are normative).

## Global Constraints

- No Ark or other vision API calls or credentials; Codex inspects the local image.
- Exactly three direct queries, each 30–50 visible observations; no colors or sizes in queries or visual features.
- Mercado filters sold count; SHEIN filters review count and rating.
- Category-only visual admission; structural differences may affect match level and ranking.
- Preserve task locking, immutable progress, evidence replay, dry-run and verified Lark write boundaries.
- Finalized historical checkpoints are read-only; unfinished Ark checkpoints are not migrated.

## Review Focus

- Ambiguous category image: reject as imagery inaccessible, never fabricate a category (Task 3).
- Invalid code/value ordering or language: reject profile before advancing checkpoint (Task 1).
- Image replacement or changed task between prepare and import: fail closed (Task 2).
- Crash after profile checkpoint but before manifest publication: identical retry repairs manifest without replacing profile (Task 2).
- Historical finalized data and partial live writes: preserve read-only reconciliation and avoid duplicate writes (Tasks 2–3).

## File map

- `scripts/visual_profile.py`: schema, taxonomy and normalized query validation; replaces Ark model/transport.
- `scripts/workflow.py`, `scripts/checkpoint.py`: two-stage preparation, provenance and category evidence.
- `scripts/candidates.py`, `scripts/models.py`: category-only admission and candidate representation.
- `SKILL.md`, `references/`, `agents/openai.yaml`: Codex operator contract and marketplace instructions.
- `tests/test_visual_profile.py`, `tests/test_workflow.py`, `tests/test_checkpoint.py`, `tests/test_candidates.py`, `tests/test_skill_contract.py`: behavior and regression coverage.

### Task 1: Visual profile and taxonomy

**Files:** Create `scripts/visual_profile.py`, `tests/test_visual_profile.py`; modify `scripts/autocomplete.py` and schema consumers.

**Interfaces:** `ProductVisualProfile.from_dict(raw: Mapping[str, object], platform: Platform) -> ProductVisualProfile`; `to_dict() -> dict[str, object]`. Expose the exact category, style, scene and selling-point mappings in the spec.

- [ ] Add failing tests for every bilingual code mapping; 1–3 style/scene and 2–6 selling-point pairs; wrong order, unknown code, duplicated values, wrong language, accent folding and query role/color/size validation.
- [ ] Run `python3 -m unittest tests.test_visual_profile -v` and verify expected failures.
- [ ] Implement immutable profile and mappings; preserve `construction` and `defining_features` as bounded free text. Ensure style/scene/point pairs match their canonical values in order.
- [ ] Run the same tests and verify they pass; update consumers to import the new model.

### Task 2: Split preparation and profile import

**Files:** Modify `scripts/workflow.py`, `scripts/checkpoint.py`, `tests/test_workflow.py`, `tests/test_checkpoint.py`.

**Interfaces:** `prepare(record_id=None, work_root=Path('.work'), dry_run=False, ...) -> str`; `import_profile(run_dir: Path, input_path: Path) -> str`; CLI `import-profile --run-dir <path> --input <path>`. New provenance is `codex_visual_profile`.

- [ ] Add failing tests proving prepare downloads without model/API calls, import validates record/platform/image binding, identical import resumes safely, changed input is rejected, malformed/oversized/symlink input fails closed, and crash recovery repairs the manifest.
- [ ] Run `python3 -m unittest tests.test_workflow tests.test_checkpoint -v` and verify the new cases fail for the intended reason.
- [ ] Implement the locked two-stage flow and durable provenance; keep finalized historical checkpoint replay read-only and reject unfinished Ark runs without rewriting them. Remove `--restart-analysis` where it conflicts with immutable import.
- [ ] Run the same suites and verify they pass; verify dry-run never writes Base or status.

### Task 3: Category-only evidence and ranking

**Files:** Modify `scripts/workflow.py`, `scripts/models.py`, `scripts/candidates.py`, their tests and evidence fixtures.

**Interfaces:** candidate detail has `visual_category_code`; source profile has `category_code`; new rejection reason is `visual_category_mismatch`.

- [ ] Add failing tests: `womens_pants_set` accepts blouse, vest and blazer with full-length pants despite sleeve/knot/leg differences; rejects skirt set, shorts set, jumpsuit and single garments; ambiguous imagery rejects; fabricated mismatch and unknown codes fail validation; metrics and ordered exhaustive processing remain enforced.
- [ ] Run `python3 -m unittest tests.test_workflow tests.test_candidates -v` and verify expected failures.
- [ ] Implement category compatibility based on visible imagery, preserving platform category text as audit-only. Keep style/structure for match level, ordering and display only; support old structural rejection only for finalized historical replay.
- [ ] Run the same suites and verify they pass, including partial-write recovery and zero-result cases.

### Task 4: Operator contract and removal of Ark

**Files:** Modify `SKILL.md`, `references/base-contract.md`, `references/browser-evidence.md`, `references/mercado-libre.md`, `references/shein.md`, `agents/openai.yaml`, fixtures and skill contract tests; replace `references/ark-vision.md`; remove obsolete Ark transport code/tests after migrating reusable validation coverage.

- [ ] Add a failing operator-contract test for image inspection, profile import, exact three-query use, category-only filter, no Ark credential/network requirement, and existing live-write authorization.
- [ ] Run `python3 -m unittest tests.test_skill_contract -v` and verify expected failures.
- [ ] Update documentation and fixtures, remove obsolete transport and configuration, and ensure all referenced paths exist. Preserve unrelated legacy read-only support where required by the spec.
- [ ] Run skill contract tests and `python3 -m py_compile scripts/*.py`; verify success.

### Task 5: Integration verification

**Files:** Existing test suites and skill artifacts.

- [ ] Run `python3 -m unittest discover -s tests -v` and `node --test tests/js/shein-collector.test.mjs`; investigate any failure before continuing.
- [ ] Run `git diff --check` and inspect `git status --short`; verify no credentials or unrelated changes.
- [ ] Exercise a representative local profile through prepare/import/evidence/dry-run using controlled fixtures; verify category-only behavior, checkpoint resume and zero/partial/full result outcomes without external writes.
- [ ] Validate the skill with the available skill validator; report any environment limitation and actual results. Do not run a live Feishu write without authorization.
