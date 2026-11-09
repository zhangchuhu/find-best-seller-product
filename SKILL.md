---
name: find-best-seller-product
description: Use when processing Feishu Base fashion research tasks that require finding same-style, similar, competitor, or bestseller products on Mercado Libre México or SHEIN US from a source image.
---

# Find Best Seller Product

Prerequisites: signed-in `lark-cli`, selected Chrome, Codex local image inspection. Never log credentials or images.

Read [Base contract](references/base-contract.md), [visual profile](references/visual-profile.md), [product evidence](references/browser-evidence.md), and [Mercado](references/mercado-libre.md) or [SHEIN](references/shein.md) rules. Unsupported platforms are rejected.

## Workflow

The first real run uses one selected record and `--dry-run`.

1. Run `python3 scripts/workflow.py prepare --record-id <RECORD_ID> --work-root .work --dry-run`. Inspect the downloaded image with Codex, write a JSON profile following the visual-profile reference, then run `python3 scripts/workflow.py import-profile --run-dir <RUN_DIR> --input <PROFILE_JSON>`. Use the validated queries without rewriting.
2. For Mercado Libre, use `chrome-extension/mercado-evidence-collector` per product-evidence reference. For SHEIN: **REQUIRED SUB-SKILL:** read the complete `chrome:control-chrome` skill before any browser action. In the explicitly selected Chrome session, search the three manifest queries directly and collect exactly three queries and 30–50 visible cards each, in order including ads. No autocomplete, query rewriting, translation, or traffic-volume claim. Within selected Chrome, use only APIs documented by `chrome:control-chrome`, including its documented `tab.playwright` API. Do not use another browser, generic web search, Python HTTP or scraping, Selenium, standalone/external Playwright, or a browser-server/hidden fallback. Do not use network interception or private APIs. After bounded SHEIN DOM failure, preserve the checkpoint; use `chrome-extension/shein-evidence-collector` only when available and permitted, otherwise request reconnect.
3. Run `python3 scripts/workflow.py validate-evidence --run-dir <RUN_DIR> --input <EVIDENCE_JSON>`.
4. Run `python3 scripts/workflow.py finalize --run-dir <RUN_DIR> --dry-run` first. Only after the intended workflow and user scope explicitly authorize live writes, repeat finalization without dry-run.

A product must recur in at least two distinct query sets. Follow the product-evidence contract. Colors/sizes may remain only in verbatim source fields; they must not enter queries, `match_level`, `visual_features`, qualification/rejection, recurrence/ranking, or result visual text.

Evidence has exactly four root fields: `task_record_id`, `platform`, `queries`, and `details`. Visible-DOM fields are authoritative; OCR cannot backfill them. Screenshot-era fields are rejected. Evidence failure stops before Base or status mutation.

Platform category text is audit-only. Assign `visual_category_code` from candidate imagery and filter by category only; use `visual_category_mismatch` for proven incompatible categories and `imagery_ambiguous_or_inaccessible` when unclear. Classify blazer and full-length pants as `womens_pants_set` before `womens_suit`. Sleeve, knot, neckline and leg shape affect similarity, not admission.

SHEIN filters only on unambiguous threshold-passing review count and rating; sold count is optional. Mercado Libre filters only on an unambiguous threshold-passing product sold display; reviews/rating are optional.

For CAPTCHA, login, or region walls, preserve the checkpoint and ask the user to resolve them in Chrome. Follow the SHEIN retry protocol.

Reject unverifiable candidates until limit or pool exhaustion. Write a verified subset below `结果数量`; only zero qualifying candidates write zero rows. Dry-run mutates neither Base nor `任务状态`.

Completion report: record ID/SKU/platform; three Codex profile queries; three per-query observation counts; recurring count; qualifying/written count; dry-run or live; blockers; whether task status changed.
