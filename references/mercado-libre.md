# Mercado Libre México evidence

Use Mercado Libre México only. Search in Spanish (`es-MX`) with the three
resolved manifest queries only, never Ark seeds. Color and size are not allowed
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
explicit ID when visible, visible detail title, and visible detail garment
category. The result title comes from the detail page, never the search card.

## Detail metrics

Use product detail only: the displayed product `vendidos`, review count, and
rating. Preserve each original display string. Normalize conservatively: `mil`
means ×1000 only in the accepted numeric grammar, and a visible plus sign is a
lower-bound count whose displayed form remains unchanged. Reject ambiguous metrics rather than estimating them.

Search-result card metrics cannot replace detail-page verification. A missing required metric rejects the candidate. Do not use seller totals or followers,
review count as sold count, a badge-only `MÁS VENDIDO`, or any other proxy.
Also reject category drift, identity change, inaccessible imagery, and garments
whose visible construction contradicts the source profile.
