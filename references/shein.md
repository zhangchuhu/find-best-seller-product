# SHEIN US evidence

Use the SHEIN US locale only. Search in English (`en-US`) with the three
direct Ark manifest queries only: the ordered Ark seeds are the final queries.
In the selected Chrome tab, navigate directly with each exact Ark manifest query;
do not translate, rewrite, autocomplete, or reorder it.
Color and size are not allowed
in queries or visual comparison; visible color/size text may remain only
verbatim evidence. Accepted product URLs use HTTPS on exactly `us.shein.com`;
other SHEIN locales and hosts are invalid.

Use the two-stage SHEIN navigation protocol in the product-evidence reference:
wait for query navigation commitment separately from visible product-grid
readiness, and reuse an accessible exact-query tab across timeouts.

## Restricted local collector

If all three bounded visible-DOM rounds fail, preserve the checkpoint and use the
installation/fallback procedure in the product-evidence reference. Ask the user to
manually load the unpacked extension at `chrome-extension/shein-evidence-collector`;
installation is never automated. Its manifest access must remain exactly
`https://us.shein.com/*`. This collector is SHEIN-only and must not be used on Mercado Libre,
another SHEIN locale, a non-SHEIN tab, or a hidden browser. It preserves the exact
Ark queries, visible first-rank order, detail identity, structured fields, screenshot
regions, and screenshot SHA-256. OCR is not a source for any structured field.

## Cards and identity

Capture visible card order without moving advertisements. Visible `Sponsored` or `Ad` means `is_ad: true`; a visibly organic card means `false`.
`is_ad` must be an unambiguous boolean; if the page does not make the state
visible, reject/stop the card rather than recording it. Use the stable goods/product identity encoded by the product URL or its accepted ID field. The explicit ID
and URL ID must agree; reject an identity change between card and detail page.
A qualified outcome preserves the canonical detail URL, explicit goods/product
ID when visible, visible detail title, and the raw category text when visible. The
result title comes from the detail page, never the search card.

## Detail metrics

Use product detail only: review count and rating are the only required filtering
metrics, and both must pass their task thresholds. Sold count is optional:
preserve its original string when visible, leave the result field empty when
absent, and never use it for filtering or ranking. Search-result card metrics
cannot replace detail-page verification. A missing required metric rejects the candidate.

Reject seller or store statistics, a Best seller badge alone, ambiguous required
review/rating metrics, identity change, inaccessible imagery, and a visual
structure mismatch where the garment's silhouette, construction, or defining
parts contradict the source profile. Category or breadcrumb wording is audit
evidence only and never overrides the image-based judgment.
