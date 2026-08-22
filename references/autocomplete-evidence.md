# Legacy autocomplete evidence contract

**Legacy compatibility only.** This reference supports reading an already
autocomplete-resolved version-2 checkpoint through the retained
`resolve-queries` compatibility command. It is not part of a new direct-Ark
run: new runs use the three final Ark manifest queries without autocomplete.

Collect this evidence only from the visible autocomplete panel in the
explicitly selected Chrome session for the target marketplace. Do not use a
marketplace API, browser storage, network interception, another browser, or a
generic search engine. A CAPTCHA, sign-in, or region wall must be resolved by
the user in that same Chrome session before collection continues.

Autocomplete display order is only a platform-derived traffic-intent proxy; it
is not numeric search volume. For each of the three prepared Ark semantic
seeds, first clear the platform's search box, then type that seed and wait for
the visible panel. Capture 1–10 visible suggestions in displayed order, with
ranks starting at 1. Never append one seed to text left by another. Record
exactly this JSON object and no additional fields:

```json
{
  "task_record_id": "rec_source",
  "platform": "shein-us",
  "seeds": [
    {
      "seed": "mini dress",
      "suggestions": [
        {"rank": 1, "text": "mini dress"},
        {"rank": 2, "text": "puff sleeve mini dress"}
      ]
    },
    {
      "seed": "puff sleeve mini dress",
      "suggestions": [
        {"rank": 1, "text": "puff sleeve mini dress"}
      ]
    },
    {
      "seed": "cocktail dress",
      "suggestions": [
        {"rank": 1, "text": "cocktail dress"}
      ]
    }
  ]
}
```

The resolver requires the supplied record ID, platform, and the three seeds to
match the prepared profile in order. It rejects missing or unknown keys,
non-list containers, invalid or non-contiguous ranks, empty/overlong strings,
and duplicate suggestions after Unicode/case/whitespace normalization.
Record IDs are trimmed and limited to 128 characters; prepared seed strings are
trimmed and limited to 120 characters; visible suggestion strings are trimmed
and limited to 160 characters.

It evaluates each block in visible rank order and selects the first suggestion
that is in the marketplace language, retains the source category without a
different garment category, satisfies that indexed seed role, contains no
color or size (including the source color), and is distinct from earlier final
queries. Block 1 uses category/subtype vocabulary only; block 2 adds
silhouette/construction evidence; block 3 adds conservative style or use-scene
evidence. Structural words such as `hem` or `dobladillo` alone are not style
evidence.

The resolver fails closed if a block has no qualifying suggestion. It never
returns a partial result and never invents, removes words from, translates,
reorders, or rewrites a suggestion. It only trims surrounding whitespace; the
selected visible spelling and internal spacing are preserved.
