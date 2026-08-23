# Mercado Libre México evidence

Use Mercado Libre México only. Search in Spanish (`es-MX`) with the three
direct Ark manifest queries only: the ordered Ark seeds are the final queries.
Color and size are not allowed
in queries or visual comparison; visible color/size text may remain only
verbatim evidence. Accepted product URLs use HTTPS and the exact host
`www.mercadolibre.com.mx` or a subdomain ending `.mercadolibre.com.mx`; other
hosts and locales are invalid.

## Cards and identity

Capture visible card order without moving advertisements. Visible
`Patrocinado` means `is_ad: true`; a visibly organic card means `false`; use
no null/unknown value: `is_ad` must be an unambiguous boolean. If the page does
not make the state visible, reject/stop the card rather than recording it. Identity is the normalized
`MLM` product identifier when encoded in the URL. An explicit ID must normalize
to the same `MLM` identity as the URL; reject an identity change between search
card and detail page. A qualified outcome preserves the canonical detail URL,
explicit ID when visible, visible detail title, and raw category text when
visible. The result title comes from the detail page, never the search card.

## Detail metrics

Use product detail only. The displayed product `vendidos` is the only required
filtering metric and must pass the task sold threshold. Normalize conservatively:
`mil` means ×1000 only in the accepted numeric grammar, and a visible plus sign
is a lower-bound count whose displayed form remains unchanged. Review count and
rating are optional: preserve their original strings when visible, leave their
result fields empty when absent, and never use them for filtering or ranking.
Reject an ambiguous required sold metric rather than estimating it.

Search-result card metrics cannot replace detail-page verification. A missing required metric rejects the candidate. Do not use seller totals or followers,
review count as sold count, a badge-only `MÁS VENDIDO`, or any other proxy.
Also reject identity change, inaccessible imagery, and a visual structure
mismatch where the garment's silhouette, construction, or defining parts
contradict the source profile. Category or breadcrumb wording is audit evidence
only and never overrides the image-based judgment.
