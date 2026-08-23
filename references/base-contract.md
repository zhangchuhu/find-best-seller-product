# Lark Base contract

This adapter is fixed to two approved Feishu Bases and always invokes `lark-cli`
as the signed-in user (`--as user`). It does not discover or accept alternate
tokens or table IDs at runtime. Every subprocess has a configurable finite
30-second production timeout by default.

## Task Base

- URL: `https://tcnae5j0r4z3.feishu.cn/base/QV65bn30QalwojsKDEicaRP9nne?table=tblwFB5IgrLwTP5P&view=vew1fRCOhk`
- Base token: `QV65bn30QalwojsKDEicaRP9nne`
- Table ID: `tblwFB5IgrLwTP5P`

| Field | Type |
|---|---|
| `SKU` | text |
| `原图` | attachment |
| `评分` | text |
| `评价数` | text |
| `结果数量` | text |
| `vendidos 数` | text |
| `平台` | text |
| `任务状态` | select |

Only the exact select value `["未开始"]` is pending. The first attachment in
Base order supplies its non-empty `file_token` and `name`. The platform may be
a plain marketplace URL or a Base Markdown link. `vendidos 数` and `评价数`
use the conservative count parser, including zero; `评分` uses the conservative
0..5 rating parser. All three cells must be text and unambiguous. `结果数量`
must be text containing a positive integer.

## Result Base

- URL: `https://tcnae5j0r4z3.feishu.cn/base/SIoUbFgwQaGumXs9U6FccFs8nYc?table=tblL8RyyMnhFeqaX&view=vew1fRCOhk`
- Base token: `SIoUbFgwQaGumXs9U6FccFs8nYc`
- Table ID: `tblL8RyyMnhFeqaX`

| Field | Type |
|---|---|
| `SKU` | text |
| `原图` | attachment |
| `平台` | text |
| `标题` | text |
| `爆款链接` | text |
| `vendidos 数` | text |
| `评价数` | text |
| `评分` | text |
| `视觉特征` | text |

The business key is exactly `(SKU, platform label, canonical 爆款链接)`. The
persisted platform labels are `Mercado Libre México` and `SHEIN US`. Duplicate
rows with the same key are an error rather than an arbitrary update target.
For SHEIN, an absent optional sold display is written as empty text. For Mercado
Libre, absent optional review-count or rating displays are written as empty text.

## CLI matrix and write boundary

Read responses must be JSON root objects with `ok: true` and an object `data`.
Raw rows are in `data.data`: each row is positionally aligned with
`data.fields` and `data.field_type_list`, while the row itself is aligned with
the same-position entry in `data.record_id_list`. All matrix lengths must
match. Record lists page in batches of 200, advancing by the actual row count;
`has_more: true` with no rows is invalid.

Attachments use only `+record-download-attachment` and
`+record-upload-attachment`. `原图` is never placed in a record-upsert JSON
payload. Result attachment identity is content-based: every result attachment
is downloaded from the fixed Result Base with the descriptor-bound, no-follow
hardened primitive and compared with the source SHA-256. A same filename is not content identity. Upload is skipped only for matching content, and readback
must contain at least one attachment whose SHA-256 matches.

Dry-run mode may list or get records and construct the intended payload, but it
must not invoke record upsert, attachment upload, or task status update. A real
result write is complete only after `+record-get` confirms all eight intended
text fields exactly and confirms a non-empty `原图` attachment list.

## Live finalization and status semantics

One private no-follow interprocess lock per task record covers prepare direct binding, evidence validation, and live finalization, including checkpoint load/compare/save, both schema
checks, exact task reread, result lookup/write, progress, status update, and
final checkpoint save. This serializes operations only inside
the supported single-host/local-work-root boundary. The fixed Result Base has
no uniqueness constraint, so cross-host atomicity is not claimed; the business
key remains unchanged.

Checkpoint JSON is read from one no-follow descriptor in capped chunks and is
limited to 10 MiB even if the file grows after its initial metadata check.

Immediately before mutation, all prepared task fields—including attachment
token/name and result limit—must equal the exact pending Base row. Changed or
non-pending rows are not overwritten. Lark transport/write, checkpoint, and
lock failures are resumable: retryable operational failures leave `任务状态` as `未开始`
and preserve completed progress. Finalization durably saves a writes-complete marker before `任务状态=成功`. If the following final checkpoint save fails, a retry
accepts only the exact prepared task in `成功`, performs read-only reconciliation
of every intended result field and source-image SHA-256, and then finalizes the
local checkpoint without duplicate writes or another status update. Only a terminal invalid pending row
selected during live preparation may be marked `失败`; dry-run, missing scope,
non-pending scope, schema, and transport failures never are.
