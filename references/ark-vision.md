# Ark vision analysis contract

`scripts.ark_vision.ArkVisionClient` analyzes one local garment image and returns
an immutable `VisualProfile`. It uses only Python's standard library and sends a
single-image multimodal Chat Completions request.

## Configuration and endpoint

Two environment variables are required and must contain non-whitespace text:

- `ARK_API_KEY`: Volcengine Ark bearer credential.
- `ARK_VISION_MODEL`: Ark vision-capable model identifier.

`ARK_VISION_TIMEOUT_SECONDS` optionally configures the request timeout in
seconds. When it is absent, the default is 300 seconds. Its value may be an
integer or decimal but must parse as a positive finite number; blank,
non-numeric, zero, negative, NaN, and infinite values fail during client
initialization before image or network I/O. A valid explicit
`ArkVisionClient(timeout=...)` argument takes priority over this environment
variable and follows the same positive-finite numeric constraint.

Configuration is validated when the client is created, before image or network
I/O. The endpoint is fixed and cannot be overridden:

`https://ark.cn-beijing.volces.com/api/v3/chat/completions`

The request uses HTTP POST, `Content-Type: application/json`, bearer
authorization, temperature zero, and JSON-object response format. Supported
image extensions are `.jpg`, `.jpeg`, `.png`, and `.webp`. Images are limited
to 20 MiB, checked before image bytes are read. The default network
opener rejects every HTTP redirect before following it, so authorization is
never forwarded to a redirect target. A final response-URL check remains as a
second line of defense.

Every request prompt includes a complete, valid JSON response reference in the
selected marketplace language. The reference demonstrates the exact object
shape only; Ark must replace every example value from the current image and
return the raw JSON object without Markdown, code fences, or prose.

## JSON profile schema

The response object must contain exactly these fields. Every scalar and every
collection item is a non-empty string after surrounding whitespace is removed.
Collection values must not contain duplicates after trimming.

| Field | JSON type | Constraint |
| --- | --- | --- |
| `category` | string | Garment category |
| `subtype` | string | More specific garment type |
| `silhouette` | string | Overall shape |
| `fit` | string | Fit description |
| `color` | string | Representative color |
| `style` | array of strings | 1–3 explicit marketplace-language style descriptions |
| `selling_points` | array of strings | 2–6 visible commercial selling points based on silhouette, construction, or decoration |
| `construction` | array of strings | 1–8 items |
| `defining_features` | array of strings | 2–5 items |
| `exclusions` | array of strings | 1–8 items describing misleading search matches to exclude |
| `use_scene` | string | Intended occasion or scene |
| `query_language` | string | `es-MX` for Mercado Libre México; `en-US` for SHEIN US |
| `query_seeds` | array of strings | Exactly 3 distinct items; each item is 3–120 characters |

The indexed seed roles are enforced from structured profile vocabulary:

1. Seed 1: core category.
2. Seed 2: category/subtype + silhouette, construction, or an explicit selling point.
3. Seed 3: category/subtype + an explicit style or use scene.

`query_seeds` are short semantic inputs to marketplace autocomplete, not final
queries. Every seed must use the selected marketplace language, contain the
garment category vocabulary, be distinct, and omit color and size terms. Color
remains descriptive source metadata only. Deterministic lexical checks reject
missing role evidence, known generic fashion phrases, obvious wrong-market
category words, category drift, and color/size vocabulary. This is a
fail-closed structured check, not a claim of perfect natural-language
detection.

## Valid Mercado Libre México example

```json
{
  "category": "vestido",
  "subtype": "vestido corto",
  "silhouette": "ajustada",
  "fit": "entallado",
  "color": "negro",
  "style": [
    "romántico",
    "elegante"
  ],
  "selling_points": [
    "manga larga",
    "escote cuadrado",
    "fruncido lateral"
  ],
  "construction": [
    "manga larga",
    "escote cuadrado"
  ],
  "defining_features": [
    "fruncido lateral",
    "dobladillo corto"
  ],
  "exclusions": [
    "vestido largo"
  ],
  "use_scene": "fiesta nocturna",
  "query_language": "es-MX",
  "query_seeds": [
    "vestido corto",
    "vestido manga larga",
    "vestido romántico"
  ]
}
```

## Valid SHEIN US example

```json
{
  "category": "dress",
  "subtype": "mini dress",
  "silhouette": "bodycon",
  "fit": "fitted",
  "color": "black",
  "style": [
    "romantic",
    "elegant"
  ],
  "selling_points": [
    "puff sleeves",
    "square neckline",
    "ruched side"
  ],
  "construction": [
    "puff sleeves",
    "square neckline"
  ],
  "defining_features": [
    "ruched side",
    "short hem"
  ],
  "exclusions": [
    "maxi length"
  ],
  "use_scene": "cocktail party",
  "query_language": "en-US",
  "query_seeds": [
    "mini dress",
    "puff sleeve mini dress",
    "romantic mini dress"
  ]
}
```

## Response and retry rules

The client accepts profile content as a plain JSON object or as one `json`
fenced block with only whitespace around it. It rejects prose, multiple fenced
blocks, non-object roots, invalid or incomplete Ark envelopes, and schema
violations.

Envelope, JSON, and profile validation failures are retried up to three total
attempts. A valid third response succeeds. The client does not retry missing
configuration, invalid timeout values, unsupported or unreadable image files,
or HTTP, URL, and timeout transport failures. After three invalid responses,
the error states the attempt count and the last validation category without
including model output.

## Secret handling

Never log, archive, return, or include in an exception the request body,
authorization header, API key, bearer credential, or image data URL/Base64.
Transport errors never incorporate exception text, URL-error reasons, or HTTP
response bodies. They use fixed allowlisted categories; an HTTP error may add
only a valid numeric status from 100 through 599. This prevents configured
keys, JSON key/token/secret assignments, Basic/custom/bearer authorization,
request or response snippets, and image data URLs from reaching raised
messages.
