# Structured Evidence with Screenshot Proof

## Status

Approved design for replacing browser-only DOM evidence with structured collection plus mandatory screenshot proof. The evidence schema remains marketplace-neutral; the first local collector is limited to SHEIN US.

## Goals

- Keep structured values as the only inputs to recurrence, filtering, ranking, and Base writes.
- Bind every search-card observation and detail outcome to visible screenshot regions.
- Reject missing, substituted, malformed, unsafe, or out-of-bounds screenshot evidence.
- Recheck screenshot integrity before live finalization.
- Avoid cookies, history, network interception, private APIs, hidden browsers, and remote code.

## Non-goals

- OCR does not populate or repair titles, URLs, IDs, ranks, ads, ratings, reviews, or sold counts.
- Screenshots do not replace structured evidence.
- The first collector does not support Mercado Libre. The validation schema will support both platforms so a separately permissioned Mercado collector can be added later.
- Existing browser retry behavior remains available for ordinary Chrome control, but it is not the source of collector output.

## Selected approach

Use overlapping viewport frames rather than one screenshot per product or one full-page image. A local Manifest V3 collector records visible structured fields, captures successive viewport screenshots as the user scrolls, and binds each item to one frame and bounding box. This preserves reviewability without producing 90–150 card images per task.

## Artifact layout

Each run keeps its evidence under the existing record run directory:

```text
<run-dir>/
├── checkpoint.json
├── evidence.json
└── screenshots/
    ├── query-1-frame-001.png
    └── detail-12345-001.png
```

The JSON root has exactly `task_record_id`, `platform`, `screenshots`, `queries`, and `details`.

Each screenshot descriptor has exactly:

- `id`: unique ASCII identifier, 1–80 characters.
- `kind`: `search` or `detail`.
- `file`: unique POSIX relative path of the form `screenshots/<name>`.
- `sha256`: lowercase 64-character digest of the exact file bytes.
- `page_url`: visible marketplace page URL at capture time.
- `query`: exact manifest query for `search`, otherwise `null`.
- `identity`: recurring marketplace identity for `detail`, otherwise `null`.

The validator derives media type, width, height, and byte size from the file. They are not trusted JSON inputs.

Each observation retains its existing structured fields and adds exactly one `evidence_ref` containing `screenshot_id` and `bbox`. A bounding box is `[x, y, width, height]` using non-negative integer pixel coordinates and positive dimensions.

Each detail retains its existing fields and adds `evidence_refs`, a non-empty list. Every reference contains exactly `purpose`, `screenshot_id`, and `bbox`. Purpose is `visual`, `metrics`, or `access_state`.

## Evidence requirements

- Every observation references a `search` screenshot bound to the same exact query and exact search page.
- A qualified detail has at least one `visual` and one `metrics` reference. One screenshot may satisfy both roles through separate boxes.
- `visual_structure_mismatch` and `imagery_ambiguous_or_inaccessible` require `visual` proof.
- `metric_missing_or_ambiguous` and `threshold_failure` require `metrics` proof.
- `identity_changed` and `detail_inaccessible` require `access_state` proof.
- New evidence cannot use legacy `category_mismatch`.
- Every detail screenshot is bound to the same recurring identity and canonical detail URL as the outcome.
- A screenshot and bounding box may support multiple records only when each record is independently inside that box or has its own box.

The structured fields remain authoritative. A screenshot can prove that a value was visible, but OCR or model interpretation cannot change the structured value during validation.

## File safety and integrity

Validation opens files relative to the run directory without following symlinks. Absolute paths, backslashes, empty segments, `.`/`..`, duplicate paths, non-regular files, and paths outside `screenshots/` are rejected.

Accepted formats are PNG, JPEG, and WebP. Type and dimensions are read from bounded file headers. Limits are:

- 8 MiB per screenshot;
- 128 screenshots per task;
- 128 MiB total screenshot bytes;
- width and height from 1 through 16,384 pixels.

The entire file is hashed through the already-open descriptor with bounded reads. The declared SHA-256 must match. Each bounding box must fit the decoded dimensions. Errors return sanitized fixed categories and never include file contents.

The canonical normalized evidence contains screenshot digests and derived metadata, so the existing evidence digest binds every screenshot. Before dry-run or live finalization, the workflow reopens every referenced screenshot and rechecks path safety, format, dimensions, size, and digest. Deletion or replacement after validation stops finalization before Base mutation.

## Checkpoint compatibility

New `validate-evidence` input always requires screenshot-backed schema. Existing `prepared` and `queries_resolved` checkpoints remain resumable and accept the new evidence.

An `evidence_validated` or `finalized` checkpoint without screenshot bindings cannot be used for a new live write. It fails with a stable instruction to restart evidence collection. No legacy evidence is silently upgraded and no screenshot reference is fabricated.

## SHEIN local collector

The repository provides an unpacked Manifest V3 extension with only:

```json
{
  "host_permissions": ["https://us.shein.com/*"]
}
```

It requests no cookie, history, webRequest, all-URL, clipboard, or remote-code capability. Screenshot capture is initiated by an explicit extension action on the active SHEIN tab. The collector:

1. verifies the current exact Ark query URL;
2. reads visible card fields and visible ad wording;
3. captures overlapping viewport frames while scrolling;
4. preserves first visible rank and deduplicates by canonical product identity;
5. stops at 30–50 cards and reports ambiguous cards instead of inventing values;
6. captures detail visual and metric regions for recurring identities;
7. exports `evidence.json` and screenshot files into a user-selected local task package.

The collector does not call marketplace APIs, intercept network traffic, inspect session storage, solve CAPTCHA, or continue through login/region walls.

## Workflow changes

`validate-evidence` parses the new exact schema, validates screenshot files before candidate derivation, normalizes screenshot metadata, and stores the screenshot-bound evidence digest.

`finalize --dry-run` and live `finalize` both replay screenshot verification. Base payloads remain unchanged: screenshots are audit artifacts and are not uploaded to Result Base. A screenshot failure leaves task status and Result Base unchanged.

The Skill instructs the operator to install and use the local collector when the selected Chrome DOM channel cannot produce reliable evidence. Completion reports include screenshot count and screenshot-validation status.

## Testing

Tests are written before implementation and cover:

- the accepted minimum schema and purpose matrix;
- missing screenshot descriptors or references;
- hash mismatch and post-validation file replacement/deletion;
- traversal, absolute path, symlink, FIFO, duplicate path, and oversized files;
- malformed/truncated PNG, JPEG, and WebP headers;
- invalid dimensions and out-of-bounds boxes;
- cross-query and cross-identity screenshot substitution;
- qualified and rejected detail role requirements;
- exact digest stability across replay;
- zero Base calls on screenshot failure;
- prepared/queries-resolved resume and legacy validated-evidence rejection;
- manifest permissions restricted to `https://us.shein.com/*`;
- Skill contract and a realistic forward-evaluation fixture.

Focused workflow, checkpoint, model, Skill-contract, and collector tests run before the full suite. A dry-run with one real SHEIN task is the acceptance test; it must not modify either Base or task status.

## Rollout

1. Add failing schema, file-safety, replay, and extension-contract tests.
2. Implement screenshot parsing and workflow validation.
3. Add the restricted SHEIN collector.
4. Update Skill references and examples.
5. Run focused and full tests.
6. Install the unpacked extension manually and run the selected Feishu record in dry-run mode.
