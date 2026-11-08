# Codex Visual Profile Design

## Intent

Replace the Volcengine Ark image-analysis call with the current Codex agent's
local image inspection. Keep deterministic validation, checkpoint recovery,
browser evidence, marketplace filtering, and Lark writes in Python.

## Goals

- Split preparation into source-image download and validated profile import.
- Remove Ark credentials, timeout configuration, transport code, and related
  operator requirements.
- Preserve exactly three final marketplace queries, with no autocomplete or
  rewriting.
- Expand common garment category vocabulary.
- Make style a closed, bilingual enumeration with stable `style_codes`.

## Non-goals

- Python will not call OpenAI or another vision API.
- Codex will not edit checkpoint files directly.
- Marketplace metric thresholds and Result Base fields remain unchanged; detail
  evidence adds `visual_category_code` and uses category-only visual filtering.
- Platform category text remains audit-only; imagery supplies the standardized
  category used for the only visual admission filter.

## Chosen approach

Use two explicit workflow commands:

```bash
python3 scripts/workflow.py prepare --record-id <RECORD_ID> --work-root .work --dry-run
python3 scripts/workflow.py import-profile --run-dir .work/<RECORD_ID> --input .work/<RECORD_ID>/visual-profile.json
```

`prepare` validates the fixed Lark schemas, reads one pending task, downloads
its source image, and persists a `prepared` checkpoint containing only the task
and bound source-image descriptor. It also writes an analysis request that
names the required profile schema and marketplace language.

The current Codex agent inspects the downloaded local image and writes
`visual-profile.json`. `import-profile` reads that file through the existing
bounded, no-follow JSON primitive, validates every field, binds it to the
prepared record and platform, persists the immutable profile, advances to
`queries_resolved`, and writes the final manifest.

Alternatives rejected:

- Re-running `prepare --profile`: conflates task acquisition with profile
  import and makes resume state unclear.
- Direct checkpoint editing by Codex: bypasses schema and transition guards.
- OpenAI API vision call: contradicts the requested current-agent workflow and
  introduces another credentialed network dependency.

## Profile schema

The imported object contains the existing visual fields plus `style_codes`:

```json
{
  "category_code": "womens_pants_set",
  "category": "conjunto",
  "subtype": "conjunto de blusa y pantalón",
  "silhouette": "pantalón de pierna ancha y blusa entallada",
  "fit": "entallado arriba y holgado abajo",
  "color": "negro",
  "style_codes": ["elegant", "modern", "semiformal"],
  "style": ["elegante", "moderno", "semiformal"],
  "selling_point_codes": ["front_knot", "high_waist", "wide_leg"],
  "selling_points": ["nudo frontal", "tiro alto", "pierna ancha"],
  "construction": ["mangas largas", "pantalón de pierna ancha"],
  "defining_features": ["nudo frontal", "conjunto de dos piezas"],
  "exclusions": ["top sin mangas"],
  "scene_codes": ["office", "semiformal_event"],
  "use_scene": ["oficina", "evento semiformal"],
  "query_language": "es-MX",
  "query_seeds": [
    "conjunto de blusa y pantalón",
    "conjunto con pantalón de pierna ancha",
    "conjunto elegante para oficina"
  ]
}
```

`style_codes` and `style` each contain one to three distinct values, have the
same length and order, and must match the exact language mapping below.

`selling_point_codes` and `selling_points` each contain two to six distinct
values, have the same length and order, and must match the exact language
mapping below.

## Closed style enumeration

| `style_code` | `es-MX` style | `en-US` style | Source |
| --- | --- | --- | --- |
| `boho` | `boho` | `boho` | image + supplement |
| `casual` | `casual` | `casual` | image |
| `party` | `fiesta` | `party` | image |
| `formal` | `formal` | `formal` | image |
| `hippie` | `hippie` | `hippie` | image |
| `youthful` | `juvenil` | `youthful` | image |
| `modern` | `moderno` | `modern` | image |
| `beach` | `playero` | `beach` | image |
| `religious` | `religioso` | `religious` | image |
| `rock` | `rockero` | `rock` | image |
| `romantic` | `romántico` | `romantic` | image |
| `understated` | `sencillo` | `understated` | image |
| `sexy` | `sexy` | `sexy` | image |
| `simple` | `simple` | `simple` | image |
| `traditional` | `tradicional` | `traditional` | image |
| `unique` | `único` | `unique` | image |
| `vintage` | `vintage` | `vintage` | image |
| `elegant` | `elegante` | `elegant` | supplement |
| `semiformal` | `semiformal` | `semi-formal` | supplement |
| `feminine` | `femenino` | `feminine` | supplement |
| `minimalist` | `minimalista` | `minimalist` | supplement |
| `classic` | `clásico` | `classic` | supplement |
| `luxury` | `lujoso` | `luxury` | supplement |
| `streetwear` | `urbano` | `streetwear` | supplement |
| `sporty` | `deportivo` | `sporty` | supplement |
| `preppy` | `colegial` | `preppy` | supplement |
| `mature` | `maduro` | `mature` | supplement |
| `y2k` | `Y2K` | `Y2K` | supplement |
| `western` | `vaquero` | `western` | supplement |
| `gothic` | `gótico` | `gothic` | supplement |

`bohemian` is accepted only as an input alias during Codex profile validation
and is canonicalized to `boho`; persisted data always uses `boho`. Mercado's
separate `Sencillo` and `Simple` values remain separate codes.

## Closed scene enumeration

`scene_codes` and `use_scene` each contain one to three distinct values, have
the same length and order, and must match the exact language mapping below.
Values visible in the supplied Mercado occasion filter are preserved even when
they overlap a style or are unusually named by the platform.

| `scene_code` | `es-MX` scene | `en-US` scene | Source |
| --- | --- | --- | --- |
| `quinceanera` | `15 años` | `quinceañera` | image |
| `baptism` | `bautizo` | `baptism` | image |
| `wedding` | `boda` | `wedding` | image |
| `casual` | `casual` | `casual` | image |
| `cocktail` | `cóctel` | `cocktail` | image |
| `everyday` | `diario` | `everyday` | image + supplement |
| `party` | `fiesta` | `party` | image |
| `mexican_party` | `fiesta mexicana` | `Mexican party` | image |
| `balloon` | `globo` | `balloon` | image |
| `graduation` | `graduación` | `graduation` | image |
| `night` | `noche` | `night` | image |
| `bridal` | `novia` | `bridal` | image |
| `office` | `oficina` | `office` | image |
| `first_communion` | `primera comunión` | `first communion` | image |
| `work` | `trabajo` | `work` | supplement |
| `business` | `negocios` | `business` | supplement |
| `semiformal_event` | `evento semiformal` | `semi-formal event` | supplement |
| `formal_event` | `evento formal` | `formal event` | supplement |
| `wedding_guest` | `invitada de boda` | `wedding guest` | supplement |
| `gala` | `gala` | `gala` | supplement |
| `prom` | `baile de graduación` | `prom` | supplement |
| `ceremony` | `ceremonia` | `ceremony` | supplement |
| `religious_event` | `evento religioso` | `religious event` | supplement |
| `night_out` | `salida nocturna` | `night out` | supplement |
| `date` | `cita` | `date night` | supplement |
| `dinner` | `cena` | `dinner` | supplement |
| `club` | `club nocturno` | `club` | supplement |
| `festival` | `festival` | `festival` | supplement |
| `vacation` | `vacaciones` | `vacation` | supplement |
| `beach` | `playa` | `beach` | supplement |
| `resort` | `resort` | `resort` | supplement |
| `travel` | `viaje` | `travel` | supplement |
| `weekend` | `fin de semana` | `weekend` | supplement |
| `school` | `escuela` | `school` | supplement |
| `campus` | `universidad` | `campus` | supplement |
| `outdoor` | `actividades al aire libre` | `outdoor` | supplement |
| `workout` | `entrenamiento` | `workout` | supplement |
| `home` | `casa` | `home` | supplement |
| `loungewear` | `descanso en casa` | `loungewear` | supplement |

The image's `Globo` value is retained as `balloon` because it is an explicit
marketplace filter value; the workflow does not reinterpret or silently drop
platform taxonomy. `Noche` and `salida nocturna` remain separate because the
first is broad while the second asserts going out. `Boda`, `invitada de boda`,
and `novia` similarly remain separate audience/occasion intents.

Codex must select only scenes visually supportable from the garment. Specific
religious, bridal, graduation, or cultural occasions are never inferred from
color alone. When no specific occasion is visually supported, use `everyday`
rather than inventing a narrow event.

## Closed selling-point enumeration

Selling points are visible garment facts, never prices, discounts, sales
claims, brands, sizes, colors, or unsupported material guesses. The closed
catalog is grouped below for maintainability, but all codes share one namespace.

### Neckline and shoulders

| `selling_point_code` | `es-MX` | `en-US` |
| --- | --- | --- |
| `v_neck` | `escote V` | `V-neck` |
| `round_neck` | `cuello redondo` | `round neck` |
| `square_neck` | `escote cuadrado` | `square neck` |
| `high_neck` | `cuello alto` | `high neck` |
| `shirt_collar` | `cuello camisero` | `shirt collar` |
| `halter_neck` | `cuello halter` | `halter neck` |
| `off_shoulder` | `hombros descubiertos` | `off shoulder` |
| `one_shoulder` | `un hombro` | `one shoulder` |
| `strapless` | `sin tirantes` | `strapless` |

### Sleeves and straps

| `selling_point_code` | `es-MX` | `en-US` |
| --- | --- | --- |
| `sleeveless` | `sin mangas` | `sleeveless` |
| `short_sleeve` | `manga corta` | `short sleeve` |
| `long_sleeve` | `manga larga` | `long sleeve` |
| `puff_sleeve` | `manga abullonada` | `puff sleeve` |
| `bell_sleeve` | `manga campana` | `bell sleeve` |
| `adjustable_straps` | `tirantes ajustables` | `adjustable straps` |

### Fit, silhouette, and length

| `selling_point_code` | `es-MX` | `en-US` |
| --- | --- | --- |
| `cropped` | `corte corto` | `cropped` |
| `bodycon` | `ajustado` | `bodycon` |
| `a_line` | `corte A` | `A-line` |
| `loose_fit` | `corte holgado` | `loose fit` |
| `oversized` | `oversize` | `oversized` |
| `wrap` | `corte cruzado` | `wrap` |
| `high_waist` | `tiro alto` | `high waist` |
| `wide_leg` | `pierna ancha` | `wide leg` |
| `straight_leg` | `pierna recta` | `straight leg` |
| `flared_leg` | `pierna acampanada` | `flared leg` |
| `palazzo` | `palazzo` | `palazzo` |
| `mini_length` | `largo mini` | `mini length` |
| `midi_length` | `largo midi` | `midi length` |
| `maxi_length` | `largo maxi` | `maxi length` |
| `full_length` | `largo completo` | `full length` |

### Construction and decoration

| `selling_point_code` | `es-MX` | `en-US` |
| --- | --- | --- |
| `front_knot` | `nudo frontal` | `front knot` |
| `tie_waist` | `lazo en cintura` | `tie waist` |
| `belted` | `con cinturón` | `belted` |
| `button_front` | `botones frontales` | `button front` |
| `zip_closure` | `cierre de cremallera` | `zip closure` |
| `ruched` | `fruncido` | `ruched` |
| `pleated` | `plisado` | `pleated` |
| `ruffled` | `volantes` | `ruffled` |
| `slit` | `abertura` | `slit` |
| `cutout` | `recorte` | `cutout` |
| `open_back` | `espalda abierta` | `open back` |
| `asymmetric` | `asimétrico` | `asymmetric` |
| `layered` | `en capas` | `layered` |
| `pockets` | `con bolsillos` | `with pockets` |
| `elastic_waist` | `cintura elástica` | `elastic waist` |
| `corset` | `corsé` | `corset` |
| `peplum` | `peplum` | `peplum` |
| `lace_trim` | `detalle de encaje` | `lace trim` |

### Material and surface effect

| `selling_point_code` | `es-MX` | `en-US` |
| --- | --- | --- |
| `satin_finish` | `acabado satinado` | `satin finish` |
| `lace` | `encaje` | `lace` |
| `sequins` | `lentejuelas` | `sequins` |
| `mesh` | `malla` | `mesh` |
| `knit` | `tejido de punto` | `knit` |
| `denim` | `mezclilla` | `denim` |
| `velvet` | `terciopelo` | `velvet` |
| `sheer` | `transparencia` | `sheer` |

Material and surface-effect codes require unambiguous visible evidence. Codex
omits them when the image cannot distinguish the material reliably.

### Pattern and set structure

| `selling_point_code` | `es-MX` | `en-US` |
| --- | --- | --- |
| `solid` | `liso` | `solid` |
| `floral_print` | `estampado floral` | `floral print` |
| `striped` | `a rayas` | `striped` |
| `plaid` | `a cuadros` | `plaid` |
| `polka_dot` | `de lunares` | `polka dot` |
| `animal_print` | `estampado animal` | `animal print` |
| `two_piece` | `dos piezas` | `two-piece` |
| `three_piece` | `tres piezas` | `three-piece` |
| `matching_set` | `conjunto coordinado` | `matching set` |

`construction` and `defining_features` remain bounded free-text fields so the
profile can preserve precise visible structure not represented by the closed
selling-point catalog. Query seed 2 may use a canonical selling point or exact
validated construction/silhouette evidence; it may not introduce an unbound
marketing phrase.

## Category vocabulary expansion

Use a closed, medium-granularity `category_code` for source and candidate
imagery. Free-text `category` and `subtype` remain marketplace-language search
vocabulary, but they do not define filtering identity.

| `category_code` | Meaning |
| --- | --- |
| `dress` | one-piece everyday, casual, party, or office dress |
| `formal_gown` | formal or gala gown |
| `jumpsuit` | one-piece top-and-trouser garment |
| `overalls` | bib-and-brace or overall garment |
| `womens_pants_set` | coordinated women's upper garment plus full-length pants |
| `womens_shorts_set` | coordinated women's upper garment plus shorts |
| `womens_skirt_set` | coordinated women's upper garment plus skirt |
| `womens_suit` | coordinated tailored jacket or blazer and lower garment |
| `top` | generic upper garment when no narrower category applies |
| `blouse` | blouse |
| `shirt` | shirt |
| `vest` | standalone vest or waistcoat |
| `blazer` | standalone blazer |
| `jacket` | jacket |
| `coat` | coat |
| `sweater` | sweater or pullover |
| `cardigan` | cardigan |
| `pants` | standalone full-length pants |
| `shorts` | standalone shorts |
| `skirt` | standalone skirt |
| `shoes` | footwear |

The requested common business terms map to language-aware search vocabulary:

| Concept | `es-MX` terms | `en-US` terms |
| --- | --- | --- |
| set | `conjunto` | `set` |
| suit | `traje` | `suit` |
| vest | `chaleco` | `vest` |
| jumpsuit | `mono` | `jumpsuit` |
| overalls | `overol`, `overoles` | `overalls` |
| shorts | `short`, `shorts` | `shorts` |
| sweater | `suéter`, `sueter` | `sweater` |
| cardigan | `cárdigan`, `cardigan` | `cardigan` |
| blazer | `blazer` | `blazer` |
| gown | `vestido de gala` | `gown` |

Category and seed comparison uses Unicode accent folding, so accented and
unaccented Spanish forms compare consistently. Persisted text remains the
canonical marketplace-language spelling.

### Category-only visual filtering

Candidate detail evidence adds `visual_category_code`, assigned from visible
candidate imagery using the same closed enumeration. Platform category or
breadcrumb text remains audit-only and never supplies this value.

For both the source image and candidate image, classify a visibly coordinated
blazer plus full-length pants as `womens_pants_set` before considering the
generic `womens_suit` category. If the lower garment cannot be determined,
do not infer full-length pants from a blazer alone.

Qualification requires source and candidate category codes to be compatible.
For `womens_pants_set`, any coordinated women's upper garment plus full-length
pants is compatible: blouse, shirt, top, vest, or blazer variations all pass.
Sleeve length, neckline, knot, decoration, fabric, fit, and pant silhouette do
not affect admission. A top-and-skirt set, top-and-shorts set, jumpsuit, dress,
or standalone garment is incompatible.

New Codex-profile runs use `visual_category_mismatch` when visible imagery
proves an incompatible category. `imagery_ambiguous_or_inaccessible` remains
the reason when imagery cannot support a category judgment. The older
`visual_structure_mismatch` value is accepted only during read-only replay of
already-finalized historical checkpoints.

Structure, style, selling points, and defining features may still determine
`match_level`, deterministic ranking, search terms, and result description;
they cannot reject a candidate after category compatibility and marketplace
metric thresholds pass.

## Query validation

The three imported queries retain the current indexed roles:

1. Category/subtype vocabulary only.
2. Category/subtype plus silhouette, construction, or selling-point evidence.
3. Category/subtype plus a canonical style or scene value.

All three must use the task marketplace language and omit colors and sizes.
The resolved checkpoint provenance becomes:

```json
{"source":"codex_visual_profile","queries":["...","...","..."]}
```

Legacy finalized checkpoints remain read-only. New runs do not accept
`ark_seeds`. No compatibility path is added for unfinished Ark checkpoints.

## Safety and recovery

- Codex output is untrusted input until `import-profile` succeeds.
- Profile import holds the same per-record lock as prepare, evidence validation,
  and live finalization.
- The source image path, record ID, task fields, platform, profile, and queries
  are immutable after import.
- Re-import of byte-equivalent normalized content is idempotent; semantic
  replacement is rejected.
- `prepare` and `import-profile` never write Result Base rows or task success.
- Dry-run and live-finalization boundaries remain unchanged.

## Removal and migration

Remove the Ark HTTP client, `ARK_API_KEY`, `ARK_VISION_MODEL`,
`ARK_VISION_TIMEOUT_SECONDS`, Ark endpoint documentation, and transport tests.
Rename the reusable schema model from `VisualProfile` to
`ProductVisualProfile`. Update skill wording, agent metadata, references,
fixtures, provenance names, CLI help, and behavioral tests to use Codex visual
profiling.

## Verification

Tests will cover download-only preparation, strict profile import, every style
mapping, category expansion and accent folding, query role validation,
idempotent resume, forged/tampered profile rejection, legacy finalized replay,
evidence validation, dry-run, live finalization, and the skill's observable
operator behavior.
