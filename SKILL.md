---
name: find-best-seller-product
description: Use when processing Feishu Base fashion research tasks that require finding same-style, similar, competitor, or bestseller products on Mercado Libre México or SHEIN US from a source image.
---

# Find Best Seller Product

Prerequisites: signed-in `lark-cli`, selected Chrome, and Ark. `ARK_API_KEY` and `ARK_VISION_MODEL` must be environment-only; `ARK_VISION_TIMEOUT_SECONDS` is positive and finite (default `300`). Never log credentials, image data, or model responses.

Read [the Base contract](references/base-contract.md), [Ark profile](references/ark-vision.md), and [product evidence](references/browser-evidence.md). Use [Mercado rules](references/mercado-libre.md) or [SHEIN rules](references/shein.md). Unsupported platforms are rejected.

## Workflow

The first real run uses one selected record and `--dry-run`.

1. Run `python3 scripts/workflow.py prepare --record-id <RECORD_ID> --work-root .work --dry-run`. Ark produces exactly three final marketplace queries, byte-for-byte equal to its ordered seeds and without color/size.
2. **REQUIRED SUB-SKILL:** read the complete `chrome:control-chrome` skill before any browser action. In the explicitly selected Chrome session, search the three manifest queries directly and collect exactly three queries and 30–50 visible cards each, in order including ads. No autocomplete collection, suggestion ranking, query rewriting, translation, or traffic-volume claim. Within selected Chrome, use only APIs documented by `chrome:control-chrome`, including its documented `tab.playwright` API. Do not use another browser, generic web search, Python HTTP or scraping, Selenium, standalone/external Playwright, or a browser-server/hidden fallback. Do not use network interception or private APIs. After bounded SHEIN DOM failure, preserve the checkpoint; use `chrome-extension/shein-evidence-collector` only when available and permitted, otherwise request reconnect, as defined in the product-evidence reference.
3. Run `python3 scripts/workflow.py validate-evidence --run-dir <RUN_DIR> --input <EVIDENCE_JSON>`.
4. Run `python3 scripts/workflow.py finalize --run-dir <RUN_DIR> --dry-run` first. Only after the intended workflow and user scope explicitly authorize live writes, repeat finalization without dry-run.

A product must recur in at least two distinct query sets. Follow the product-evidence fields, outcome order, thresholds, and features. Colors/sizes may remain only in verbatim source fields; they must not enter queries, `match_level`, `visual_features`, qualification/rejection, recurrence/ranking, or result visual text.

Structured evidence has exactly four root fields: `task_record_id`, `platform`, `queries`, and `details`. Structured visible-DOM fields are authoritative; OCR cannot backfill them. Screenshot-era fields are rejected rather than ignored. Any evidence failure stops before Result Base or `任务状态` mutation.

Platform category text is audit-only. Compare imagery by silhouette, construction, and defining garment parts; reject contradictions as `visual_structure_mismatch`.

SHEIN filters only on unambiguous threshold-passing review count and rating; sold count is optional. Mercado Libre filters only on an unambiguous threshold-passing product sold display; reviews/rating are optional.

For CAPTCHA, login, authentication, or region walls, preserve the checkpoint and ask the user to resolve them in selected Chrome. Before SHEIN collection/resume, follow task-tab cleanup and the three-round retry protocol.

Reject unverifiable candidates until the limit or pool exhaustion. Write a verified subset below `结果数量`; only zero qualifying candidates write zero rows. Dry-run mutates neither Base nor `任务状态`.

Completion report: record ID/SKU/platform; three direct Ark queries; three per-query observation counts; recurring count; qualifying/written count; dry-run or live; blockers; whether task status changed.
