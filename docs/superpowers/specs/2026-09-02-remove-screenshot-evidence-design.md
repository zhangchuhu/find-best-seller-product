# Remove Screenshot Evidence Design

## Goal

Remove screenshot capture, storage, references, and validation from the product-research workflow while retaining strict validation of browser-collected structured data.

## Scope

New evidence has exactly four root fields:

- `task_record_id`
- `platform`
- `queries`
- `details`

The workflow must reject screenshot-era fields, including `screenshots`, `evidence_ref`, `evidence_refs`, `screenshot_manifest`, and `screenshot_count`. There is no migration, compatibility mode, or read-only replay for historical screenshot-backed evidence or checkpoints.

## Architecture

Browser collection remains the source of marketplace facts. The selected Chrome session collects visible search-card fields and visible product-detail fields into `evidence.json`. Screenshots, OCR, image files, bounding boxes, screenshot descriptors, and screenshot hashes are not produced or consumed.

`validate-evidence` validates the structured document against the prepared task and direct Ark query manifest. It continues to enforce exact task/platform binding, exactly three ordered queries, per-query observation limits and order, canonical product identity, cross-query recurrence, platform-specific metric rules, visual-structure outcomes, and deterministic candidate ordering.

`finalize` replays the canonical structured evidence and checkpoint state before dry-run output or live Base writes. It does not open or validate screenshot files.

## Data Contract

Each `queries` item contains its exact Ark query and ordered `observations`. Each observation retains the structured card fields required by the current platform contract, but it must not contain `evidence_ref`.

Each `details` item retains identity, outcome, title, canonical URL/product ID, platform metrics, match level, and color/size-free visual structure. It must not contain `evidence_refs`.

Validated checkpoint evidence binds the canonical structured payload, candidate set, counts, provenance, and progress. Screenshot manifest/count fields are removed from the immutable checkpoint core and digest input.

## Chrome Collector

The SHEIN helper extension becomes a DOM-only collector. It may read visible page content and export `evidence.json`; it must not capture tabs, generate image data, calculate screenshot hashes, create a `screenshots/` directory, or download screenshot files. Permissions and UI copy must match that reduced behavior.

## Failure Behavior

Validation fails closed before Result Base or task-status mutation when:

- the root shape is not exactly the four required fields;
- any screenshot-era field appears at any supported evidence level;
- task, platform, query manifest, identity, counts, metrics, visual structure, or ordering is invalid;
- a checkpoint uses the removed screenshot-era contract.

Historical screenshot-backed evidence and checkpoints are unsupported and rejected. Operators must start a fresh run and recollect structured evidence.

## Code Removal and Documentation

Delete the standalone screenshot validator and its dedicated tests. Remove screenshot branches from workflow/checkpoint validation, fixtures, helper functions, the SHEIN extension, Skill instructions, platform references, browser-evidence schema, metadata, and forward-test contracts. Retain no dormant compatibility path.

## Testing

Use test-driven development:

1. Add failing contract tests proving pure structured evidence validates and screenshot-era fields are rejected.
2. Add failing checkpoint/replay tests proving the new immutable core contains no screenshot metadata and old screenshot checkpoints fail.
3. Add failing extension tests proving export is JSON-only and screenshot capture is absent.
4. Implement the smallest production changes that make each test group pass.
5. Update Skill behavior tests and documentation only after their new tests fail.
6. Run focused Python and Node suites, syntax/compile checks, then the full repository suite.

## Acceptance Criteria

- A valid DOM-only evidence document can pass `validate-evidence` and `finalize --dry-run` without any image file.
- Screenshot fields are rejected rather than ignored.
- No normal or historical workflow reads, writes, hashes, or validates screenshots.
- The extension exports only structured `evidence.json`.
- Dry-run and live-write safety invariants remain unchanged.
- All updated automated tests pass.
