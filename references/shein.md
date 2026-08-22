# SHEIN US evidence

Use the SHEIN US locale only. Search in English (`en-US`) with the three
direct Ark manifest queries only: the ordered Ark seeds are the final queries.
Color and size are not allowed
in queries or visual comparison; visible color/size text may remain only
verbatim evidence. Accepted product URLs use HTTPS on exactly `us.shein.com`;
other SHEIN locales and hosts are invalid.

## Cards and identity

Capture visible card order without moving advertisements. Visible `Sponsored` or `Ad` means `is_ad: true`; a visibly organic card means `false`.
`is_ad` must be an unambiguous boolean; if the page does not make the state
visible, reject/stop the card rather than recording it. Use the stable goods/product identity encoded by the product URL or its accepted ID field. The explicit ID
and URL ID must agree; reject an identity change between card and detail page.
A qualified outcome preserves the canonical detail URL, explicit goods/product
ID when visible, visible detail title, and visible detail garment category. The
result title comes from the detail page, never the search card.

## Detail metrics

Use product detail only: product sold count, review count, and rating. Preserve
the original displayed strings and normalize only unambiguous numeric forms.
Search-result card metrics cannot replace detail-page verification. A missing required metric rejects the candidate.

Reject seller or store statistics, review count used as sold count, a Best seller badge alone, ambiguous metrics, category drift, identity change,
inaccessible imagery, and garments whose visible construction contradicts the
source profile.
