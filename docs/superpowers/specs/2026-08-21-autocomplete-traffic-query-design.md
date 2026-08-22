# Platform Autocomplete Traffic-Query Design

## Context

The current workflow asks Ark Vision to produce three complete marketplace
queries. This produced overly specific phrases containing color and other
low-value attributes. Those phrases are not grounded in the marketplace's
current search language and can reduce recall.

This change separates visual analysis from query resolution. Ark supplies
short semantic seeds; the explicitly selected Chrome session supplies visible
marketplace autocomplete suggestions; deterministic validation selects the
three final search queries.

Autocomplete order is treated only as a platform-derived traffic-intent signal.
The workflow does not claim exact search volume.

## Goals

- Derive final queries from visible autocomplete suggestions on Mercado Libre
  México or SHEIN US.
- Exclude color and size from seeds, suggestions selected as final queries, and
  later visual matching or rejection decisions.
- Preserve exactly three final queries, 30–50 visible search cards per query,
  cross-query recurrence, detail verification, resumability, and Base write
  semantics.
- Preserve the explicit Chrome-only boundary and treat page content as
  untrusted evidence.

## Non-goals

- Measuring or claiming numeric search volume.
- Using Google Trends, external SEO services, generic web search, or hidden
  browser automation.
- Changing either Feishu Base schema.
- Treating color or size agreement as a positive or negative similarity signal.

## Data model

`VisualProfile` keeps `color` as descriptive source metadata but removes final
`queries`. It adds exactly three `query_seeds`, in this indexed order:

1. Core category seed.
2. Silhouette or construction seed.
3. Style or use-scene seed.

Every seed is short, marketplace-language text containing a garment category.
Seeds must be distinct and must not contain color or size terms. Construction,
silhouette, defining features, exclusions, and use scene remain available for
non-color, non-size visual comparison.

Autocomplete evidence has exactly three ordered blocks:

```json
{
  "task_record_id": "rec_source",
  "platform": "shein-us",
  "seeds": [
    {
      "seed": "puff sleeve mini dress",
      "suggestions": [
        {"rank": 1, "text": "puff sleeve mini dress"},
        {"rank": 2, "text": "puff sleeve cocktail dress"}
      ]
    }
  ]
}
```

The submitted file contains all three blocks. Each block contains 1–10 visible,
contiguous, ordered suggestions captured verbatim from the target platform's
search box. Duplicate suggestion text after normalization is invalid.

Resolved-query state stores the normalized autocomplete evidence and exactly
three final queries, one per seed block. A final query must equal a visible
suggestion verbatim after surrounding-whitespace normalization.

## Deterministic selection

For each seed block, inspect suggestions in visible rank order and select the
first suggestion that:

- uses the platform language;
- contains the source garment category without category drift;
- contributes the block's intended category, silhouette/construction, or
  style/use-scene role;
- contains no color or size term; and
- is distinct from already selected final queries.

If no suggestion qualifies, query resolution fails without advancing the
checkpoint. The agent must not invent or rewrite a suggestion.

Color-term rejection covers source colors plus maintained marketplace color
vocabulary and common compounds. Size-term rejection covers numeric sizing,
letter sizing, plus-size/petite/tall labels, and marketplace-language size
vocabulary. These vocabularies are conservative and test-backed; ambiguous
terms fail closed rather than being silently removed.

## Workflow and state transitions

The workflow becomes:

```text
prepare
  -> prepared (source profile + three query seeds)
collect autocomplete in selected Chrome
resolve-queries
  -> queries_resolved (autocomplete evidence + three final queries)
collect 30–50 search cards per final query in selected Chrome
validate-evidence
  -> evidence_validated
finalize
  -> finalized
```

`prepare` writes a manifest containing `query_seeds`; it does not claim final
queries. A new command accepts an autocomplete JSON file and writes the
`queries_resolved` checkpoint stage plus an updated manifest containing both
seeds and final queries.

`validate-evidence` requires `queries_resolved` and accepts only the exact final
queries stored there. It never accepts Ark seeds as search queries. Resume from
any stage reuses immutable prior evidence. A changed task, source attachment,
platform, seed set, or autocomplete evidence fails closed.

## Chrome collection

Use only the explicitly selected Chrome session and the documented
`tab.playwright` surface. For each seed:

1. Navigate to the target marketplace search page or focus its own search box.
2. Clear the field and type the seed.
3. Wait for the visible autocomplete panel.
4. Capture the first 1–10 visible suggestion strings in displayed order.

Do not use network interception, private marketplace APIs, cookies, storage,
generic search, another browser, or standalone Playwright. CAPTCHA, login,
authentication, and region walls preserve the checkpoint and require user
resolution in the same Chrome session.

## Visual filtering

Candidate comparison continues to use garment category, subtype, silhouette,
fit, construction, defining features, exclusions, and use scene. Color and size
must not affect `match_level`, `visual_features`, qualification, rejection
reason, or ranking. Result `视觉特征` must omit color and size claims even when
they are visible.

## Compatibility and migration

Existing finalized checkpoints remain readable. Existing prepared or
evidence-validated checkpoints using Ark-generated final queries are rejected
by the new workflow because they lack autocomplete provenance; the record must
restart analysis under the new workflow.

The two Base contracts and result business key remain unchanged.

## Error handling

- Missing, hidden, empty, duplicated, reordered, wrong-language, color-bearing,
  size-bearing, or category-drifting suggestions reject query resolution.
- Query resolution never mutates either Base.
- Search evidence collected before successful query resolution is invalid.
- Operational errors remain resumable and leave `任务状态` unchanged.
- Error messages remain bounded and must not include cookies, credentials,
  image data, or untrusted page content beyond safe field labels.

## Testing

- Ark prompt and profile tests prove seeds replace final queries and prohibit
  color and size terms.
- Resolver tests cover visible-order selection, exact suggestion provenance,
  role coverage, language, category drift, duplicates, color vocabulary, size
  vocabulary, and no-valid-suggestion failure.
- Workflow tests cover the new state transition, resume, stale/changed input,
  legacy checkpoint behavior, and exact final-query binding.
- Candidate tests prove color and size do not influence matching, features,
  qualification, rejection, or ranking.
- Skill contract tests require Chrome autocomplete collection before product
  search and forbid invented traffic terms.
- An independent forward test presents a colored source garment and verifies
  that the resulting final queries and visual result features omit color and
  size while preserving category and construction intent.
