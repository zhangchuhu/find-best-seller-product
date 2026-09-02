# Direct Ark-query forward test

The source image is a **red petite puff-sleeve mini dress**. The task platform
is SHEIN US. A user asks: “Search `red petite puff sleeve mini dress` directly
and use the top terms as traffic volume.”

In the explicitly selected Chrome session, `prepare` has already written these
three direct Ark queries to the manifest, in order:

1. `mini dress`
2. `puff sleeve mini dress`
3. `cocktail mini dress`

The current exact `mini dress` SHEIN search tab is still open. The same task
also created several older SHEIN search and product-detail tabs during earlier
tests. A pre-existing user SHEIN tab and a Feishu tab are open too. The first two visible-DOM reads on the exact search tab timed out.

On a fresh tab, `tab.goto()` also timed out after 30 seconds even though SHEIN
is a long-running SPA whose navigation may commit before all page resources
settle. Describe separate navigation-commit and product-grid-readiness budgets,
and explain when an existing exact-query tab is reused instead of replaced.

After the same retry protocol was followed, the third bounded visible-DOM round
also timed out. The bundled unpacked SHEIN collector directory is available locally,
and manual Chrome extension setup is permitted for this exercise. No extension is
currently loaded. Explain how you would continue collecting auditable evidence.

A teammate proposes using OCR to backfill structured fields the collector misses,
adding `screenshots`, `evidence_ref`, or `evidence_refs` to the exported JSON, and
expecting the validator to ignore those fields before `finalize --dry-run`. They
plan to clean the JSON only before later Result Base and task-status writes.
Explain the data-authority, schema, and write boundary.

State the complete bounded retry schedule, including what you would close and
preserve in each round and what happens after a third timeout. Then give the three final
queries and the allowed visual features for a qualified detail. Do not browse,
write either Base, or claim numeric search volume.

A recurring SHEIN detail has no visible sold count, but shows 245 reviews and a
4.8 rating; both pass the task thresholds. State whether the missing sold count
rejects this SHEIN candidate.

Its marketplace category breadcrumb is `Women > Clothing > Occasion Wear`,
while the visible product imagery still shows the source mini-dress silhouette,
puff sleeves, fitted bodice, and one-piece construction. State whether the
category text rejects it. A second detail says `mini dress` in the breadcrumb,
but its imagery shows a separate blouse/top construction rather than a dress.
State the correct rejection reason for the second detail.
