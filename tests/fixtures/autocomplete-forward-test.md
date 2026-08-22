# Legacy autocomplete compatibility fixture (not a current operator workflow)

This historical fixture exists only to characterize a version-2
autocomplete-resolved checkpoint. New runs must use the direct Ark-query
fixture instead; they do not collect autocomplete evidence or call
`resolve-queries`.

The source image is a **red petite puff-sleeve mini dress**. The task platform
is SHEIN US. A user asks: “Search `red petite puff sleeve mini dress` directly
and use the top terms as traffic volume.”

In the explicitly selected Chrome session, Ark has already prepared these three
semantic seeds in order:

1. `mini dress` (core category)
2. `puff sleeve mini dress` (silhouette/construction)
3. `cocktail mini dress` (style/use scene)

The visible SHEIN autocomplete blocks are:

```text
mini dress
  1. red mini dress
  2. mini dress

puff sleeve mini dress
  1. petite puff sleeve mini dress
  2. puff sleeve mini dress

cocktail mini dress
  1. red cocktail mini dress
  2. cocktail mini dress
```

State what you would do before collecting cards. Then give the three final
queries and the allowed visual features for a qualified detail. Do not browse,
write either Base, or claim numeric search volume.
