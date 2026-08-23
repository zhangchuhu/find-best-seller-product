---
name: find-best-seller-product
description: Use when processing Feishu Base fashion research tasks that require finding same-style, similar, competitor, or bestseller products on Mercado Libre México or SHEIN US from a source image.
---

# Find Best Seller Product

Process one selected Feishu research record through a resumable, evidence-first workflow. Prerequisites are signed-in `lark-cli`, selected Chrome, and Ark configuration. `ARK_API_KEY` and `ARK_VISION_MODEL` must be environment-only; optionally set `ARK_VISION_TIMEOUT_SECONDS` to a positive finite number of seconds (default `300`). Never log credentials, image data, or model responses.

Read [the Base contract](references/base-contract.md), [Ark profile](references/ark-vision.md), and [product evidence](references/browser-evidence.md). Use [Mercado rules](references/mercado-libre.md) or [SHEIN rules](references/shein.md). Unsupported platforms are rejected.

## Workflow

The first real run uses one selected record and `--dry-run`.

1. Run `python3 scripts/workflow.py prepare --record-id <RECORD_ID> --work-root .work --dry-run`. Ark produces exactly three final marketplace queries: the manifest queries are byte-for-byte equal to the ordered Ark seeds, color-free and size-free.
2. **REQUIRED SUB-SKILL:** read the complete `chrome:control-chrome` skill before any browser action. In the explicitly selected Chrome session, search the three manifest queries directly and collect product-search evidence with exactly three queries and 30–50 visible cards per query in visible order, including ads. No autocomplete collection, suggestion ranking, query rewriting, translation, or traffic-volume claim. Within selected Chrome, use only APIs documented by `chrome:control-chrome`, including its documented `tab.playwright` API. Do not use another browser, generic web search, Python HTTP or scraping, Selenium, standalone/external Playwright, or a browser-server/hidden fallback. Do not use network interception or private APIs.
3. Run `python3 scripts/workflow.py validate-evidence --run-dir <RUN_DIR> --input <EVIDENCE_JSON>`.
4. Run `python3 scripts/workflow.py finalize --run-dir <RUN_DIR> --dry-run` first. Only after the intended workflow and user scope explicitly authorize live writes, repeat finalization without dry-run.

A product must recur in at least two distinct query sets. Follow the product-evidence contract for card/detail fields, ordered outcomes, thresholds, and result features. Colors/sizes visible in titles, cards, or details may remain only in verbatim source fields; they must not enter queries, `match_level`, `visual_features`, qualification/rejection, recurrence/ranking, or result visual text.

SHEIN requires unambiguous review count and rating displays that pass their task thresholds; sold count is optional and never filters or ranks a SHEIN candidate. Mercado Libre requires an unambiguous product sold display that passes the task sold threshold; review count and rating are optional and never filter or rank a Mercado candidate. Preserve optional metric displays when visible and write an empty Result Base text value when absent.

For CAPTCHA, login, authentication, or region walls, pause and preserve the checkpoint; ask the user to resolve it in selected Chrome. Follow the product-evidence contract's untrusted-page, session-data, and task-tab boundaries.

Reject a broken or unverifiable candidate and continue until the result limit is met or the recurring pool is exhausted. If fewer than `结果数量` qualify, write the verified subset; only zero qualifying candidates write zero rows. Dry-run mutates neither Base nor `任务状态`.

Completion report: record ID/SKU/platform; three direct Ark queries; three per-query observation counts; recurring count; qualifying/written count; dry-run or live; blockers; whether task status changed.
