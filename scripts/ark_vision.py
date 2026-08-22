"""Secret-safe Volcengine Ark visual profiling client."""

from __future__ import annotations

import base64
import json
import math
import os
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping as MappingABC, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .platforms import Platform
from .query_terms import contains_color_or_size


ARK_CHAT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_PROFILE_FIELDS = (
    "category",
    "subtype",
    "silhouette",
    "fit",
    "color",
    "style",
    "selling_points",
    "construction",
    "defining_features",
    "exclusions",
    "use_scene",
    "query_language",
    "query_seeds",
)
_COLLECTION_LIMITS = {
    "style": (1, 3),
    "selling_points": (2, 6),
    "construction": (1, 8),
    "defining_features": (2, 5),
    "exclusions": (1, 8),
    "query_seeds": (3, 3),
}
_SCALAR_FIELDS = (
    "category",
    "subtype",
    "silhouette",
    "fit",
    "color",
    "use_scene",
    "query_language",
)
_LANGUAGE_BY_PLATFORM = {
    Platform.MERCADO_MX: "es-MX",
    Platform.SHEIN_US: "en-US",
}
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 300.0
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_GENERIC_SEEDS = {
    "women fashion", "womens fashion", "generic trendy clothing",
    "moda mujer", "ropa de moda generica", "ropa femenina",
}
_CATEGORY_TERMS = {
    "dress", "skirt", "top", "blouse", "shirt", "pants", "trousers",
    "jacket", "coat", "shoes", "vestido", "falda", "top", "blusa",
    "camisa", "pantalon", "pantalones", "chaqueta", "abrigo", "zapatos",
}
_WRONG_LANGUAGE_TERMS = {
    "es-MX": {"dress", "skirt", "blouse", "shirt", "pants", "trousers", "jacket", "coat", "shoes"},
    "en-US": {"vestido", "falda", "blusa", "camisa", "pantalon", "pantalones", "chaqueta", "abrigo", "zapatos"},
}
class ArkVisionError(RuntimeError):
    """Raised for sanitized configuration, transport, and response failures."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Convert every redirect into an HTTPError without contacting its target."""

    def redirect_request(
        self,
        request: object,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


_DEFAULT_OPENER = urllib.request.build_opener(_RejectRedirects()).open


@dataclass(frozen=True)
class VisualProfile:
    category: str
    subtype: str
    silhouette: str
    fit: str
    color: str
    style: tuple[str, ...]
    selling_points: tuple[str, ...]
    construction: tuple[str, ...]
    defining_features: tuple[str, ...]
    exclusions: tuple[str, ...]
    use_scene: str
    query_language: str
    query_seeds: tuple[str, str, str]

    def __post_init__(self) -> None:
        for name in _SCALAR_FIELDS:
            object.__setattr__(self, name, _normalized_string(getattr(self, name), name))
        if self.query_language not in frozenset(_LANGUAGE_BY_PLATFORM.values()):
            raise ValueError("query_language must be a supported marketplace label")
        for name, (minimum, maximum) in _COLLECTION_LIMITS.items():
            values = _normalized_collection(
                getattr(self, name), name, minimum=minimum, maximum=maximum,
            )
            if name == "query_seeds" and any(not 3 <= len(value) <= 120 for value in values):
                raise ValueError("query_seeds items must contain 3-120 characters")
            object.__setattr__(self, name, values)
        _validate_query_seed_roles(self)

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, object], platform: Platform,
    ) -> "VisualProfile":
        if not isinstance(raw, MappingABC):
            raise TypeError("visual profile must be a mapping")
        if not isinstance(platform, Platform):
            raise TypeError("platform must be a Platform")
        supplied = set(raw.keys())
        required = set(_PROFILE_FIELDS)
        if supplied != required:
            missing = required - supplied
            unknown = supplied - required
            if missing:
                raise ValueError("visual profile has missing fields")
            if unknown:
                raise ValueError("visual profile has unknown fields")
        profile = cls(**{name: raw[name] for name in _PROFILE_FIELDS})
        if profile.query_language != _LANGUAGE_BY_PLATFORM[platform]:
            raise ValueError("query_language does not match the marketplace")
        return profile

    def to_dict(self) -> dict[str, object]:
        result = {name: getattr(self, name) for name in _PROFILE_FIELDS}
        for name in (
            "style", "selling_points", "construction", "defining_features",
            "exclusions", "query_seeds",
        ):
            result[name] = list(result[name])
        return result


def _normalized_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _normalized_collection(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence of strings")
    normalized = tuple(_normalized_string(item, f"{field} item") for item in value)
    if not minimum <= len(normalized) <= maximum:
        if minimum == maximum:
            raise ValueError(f"{field} must contain exactly {minimum} items")
        raise ValueError(f"{field} must contain {minimum}-{maximum} items")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


def _words(value: str) -> set[str]:
    return {word.casefold() for word in _WORD_RE.findall(value)}


def _attribute_words(values: Sequence[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        result.update(_words(value))
    return result


def _validate_query_seed_roles(profile: VisualProfile) -> None:
    seed_words = [_words(seed) for seed in profile.query_seeds]
    category_words = _words(profile.category) | _words(profile.subtype)
    silhouette_words = _words(profile.silhouette)
    construction_words = _attribute_words(profile.construction)
    selling_point_words = _attribute_words(profile.selling_points)
    profile_style_words = _attribute_words(profile.style)
    use_scene_words = _words(profile.use_scene)
    target_categories = category_words & _CATEGORY_TERMS
    forbidden_language = _WRONG_LANGUAGE_TERMS[profile.query_language]

    for seed, words in zip(profile.query_seeds, seed_words, strict=True):
        normalized = " ".join(seed.casefold().split())
        if normalized in _GENERIC_SEEDS:
            raise ValueError("query_seeds must not use generic fashion phrases")
        if words & forbidden_language:
            raise ValueError("query_seeds contain obvious wrong-market language")
        if not words & category_words:
            raise ValueError("every query seed must agree lexically with category or subtype")
        drift = (words & _CATEGORY_TERMS) - target_categories
        if drift:
            raise ValueError("query_seeds contain category drift")
        if contains_color_or_size(seed, profile.query_language, profile.color):
            raise ValueError("query_seeds must not contain color or size")

    if not seed_words[1] & (
        silhouette_words | construction_words | selling_point_words
    ):
        raise ValueError("query seed 2 must contain silhouette, construction, or selling-point evidence")
    if seed_words[0] - category_words:
        raise ValueError("query seed 1 must use category or subtype vocabulary only")
    if not (
        seed_words[2] & use_scene_words
        or seed_words[2] & profile_style_words
    ):
        raise ValueError("query seed 3 must contain style or use-scene evidence")


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ArkVisionError(f"missing required environment variable: {name}")
    return value.strip()


def _configured_timeout(environ: Mapping[str, str]) -> float:
    raw = environ.get("ARK_VISION_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw.strip())
    except (AttributeError, TypeError, ValueError):
        raise ArkVisionError(
            "ARK_VISION_TIMEOUT_SECONDS must be a positive finite number",
        ) from None
    if not math.isfinite(value) or value <= 0:
        raise ArkVisionError(
            "ARK_VISION_TIMEOUT_SECONDS must be a positive finite number",
        )
    return value


def _prompt(platform: Platform) -> str:
    language = "es-MX" if platform is Platform.MERCADO_MX else "en-US"
    if platform is Platform.MERCADO_MX:
        reference = {
            "category": "vestido",
            "subtype": "vestido corto",
            "silhouette": "línea A",
            "fit": "ajuste regular",
            "color": "rojo",
            "style": ["romántico", "elegante"],
            "selling_points": ["manga abullonada", "escote cuadrado", "detalle de lazo"],
            "construction": ["manga abullonada", "escote cuadrado"],
            "defining_features": ["detalle de lazo", "falda en capas"],
            "exclusions": ["largo maxi"],
            "use_scene": "cóctel",
            "query_language": "es-MX",
            "query_seeds": [
                "vestido corto",
                "vestido manga abullonada",
                "vestido romántico",
            ],
        }
    else:
        reference = {
            "category": "dress",
            "subtype": "mini dress",
            "silhouette": "A-line",
            "fit": "regular fit",
            "color": "red",
            "style": ["romantic", "elegant"],
            "selling_points": ["puff sleeves", "square neckline", "bow detail"],
            "construction": ["puff sleeves", "square neckline"],
            "defining_features": ["bow detail", "tiered skirt"],
            "exclusions": ["maxi length"],
            "use_scene": "cocktail",
            "query_language": "en-US",
            "query_seeds": [
                "mini dress",
                "puff sleeve mini dress",
                "romantic mini dress",
            ],
        }
    reference_json = json.dumps(reference, ensure_ascii=False, indent=2)
    return (
        "Analyze only the garment visible in the supplied image. Return only the raw JSON object; "
        "do not add Markdown, code fences, headings, explanations, or any other prose. "
        "The object must contain exactly these keys and no others: category, subtype, "
        "silhouette, fit, color, style, selling_points, construction, defining_features, "
        "exclusions, use_scene, query_language, query_seeds. All scalar values and list "
        "items must be non-empty strings. style must contain 1-3 distinct marketplace-"
        "language style descriptions. selling_points must contain 2-6 distinct visible "
        "commercial selling points based on silhouette, construction, or decoration. "
        "construction must contain 1-8 distinct items; defining_features must contain "
        "2-5 distinct items; exclusions must contain 1-8 distinct items. query_seeds "
        "must contain exactly 3 distinct strings, each "
        f"3-120 characters. Set query_language to {language} and write every seed "
        f"for the {language} marketplace. These are semantic seeds, not final queries. "
        "Seed 1 must use only category/subtype words. Seed 2 must repeat the category "
        "and include words copied from silhouette, selling_points, or construction. "
        "Seed 3 must repeat the category and include words copied from style or use_scene. "
        "Never include color or size in any seed, in either English or Spanish. Color is descriptive "
        "source metadata only. Never use generic fashion phrases or drift to another "
        "garment category. Before returning, silently verify all 13 keys, list counts, "
        "three distinct role-correct seeds, the fixed language, and the color/size ban. "
        "RESPONSE REFERENCE — use this exact JSON structure but replace every example value "
        "with values derived from the current image; never copy an example value merely "
        "because it appears below:\n"
        f"{reference_json}"
    )


class _ValidationFailure(Exception):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON field")
        result[name] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-standard JSON number")


def _load_json(value: str | bytes, category: str) -> object:
    try:
        text = value.decode("utf-8") if isinstance(value, bytes) else value
        if not isinstance(text, str):
            raise TypeError
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        raise _ValidationFailure(category) from None


def _extract_content(body: str | bytes) -> str:
    envelope = _load_json(body, "envelope")
    if not isinstance(envelope, dict):
        raise _ValidationFailure("envelope")
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _ValidationFailure("envelope")
    first = choices[0]
    if not isinstance(first, dict):
        raise _ValidationFailure("envelope")
    message = first.get("message")
    if not isinstance(message, dict):
        raise _ValidationFailure("envelope")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise _ValidationFailure("envelope")
    return content


def _extract_profile_object(content: str) -> Mapping[str, object]:
    text = content.strip()
    if text.startswith("```") or text.endswith("```") or "```" in text:
        match = re.fullmatch(r"```json[ \t]*\r?\n(.*?)\r?\n```", text, re.DOTALL)
        if match is None:
            raise _ValidationFailure("json")
        text = match.group(1)
    raw = _load_json(text, "json")
    if not isinstance(raw, dict):
        raise _ValidationFailure("json")
    return raw


class ArkVisionClient:
    def __init__(
        self,
        environ: Mapping[str, str] = os.environ,
        opener: Callable[..., object] = _DEFAULT_OPENER,
        timeout: float | None = None,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    ) -> None:
        self._api_key = _required_environment(environ, "ARK_API_KEY")
        self._model = _required_environment(environ, "ARK_VISION_MODEL")
        if timeout is None:
            timeout = _configured_timeout(environ)
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        if not callable(opener):
            raise TypeError("opener must be callable")
        if isinstance(max_image_bytes, bool) or not isinstance(max_image_bytes, int) or max_image_bytes <= 0:
            raise ValueError("max_image_bytes must be a positive integer")
        self._opener = opener
        self._timeout = float(timeout)
        self._max_image_bytes = max_image_bytes

    def analyze(self, image_path: Path, platform: Platform) -> VisualProfile:
        if not isinstance(platform, Platform):
            raise ArkVisionError("platform must be a supported Platform")
        path = Path(image_path)
        mime = _MIME_BY_SUFFIX.get(path.suffix.lower())
        if mime is None:
            raise ArkVisionError("unsupported image extension")
        try:
            status = path.stat()
            if path.is_symlink() or not path.is_file() or status.st_size <= 0:
                raise OSError
            if status.st_size > self._max_image_bytes:
                raise ArkVisionError("image exceeds the configured size limit")
            with path.open("rb") as image_file:
                image_bytes = image_file.read(self._max_image_bytes + 1)
            if len(image_bytes) > self._max_image_bytes:
                raise ArkVisionError("image exceeds the configured size limit")
            encoded = base64.b64encode(image_bytes).decode("ascii")
        except ArkVisionError:
            raise
        except (OSError, ValueError):
            raise ArkVisionError("unable to read image") from None
        payload = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _prompt(platform)},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{encoded}"},
                    },
                ],
            }],
        }
        request = urllib.request.Request(
            ARK_CHAT_ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        last_category = "response"
        for _attempt in range(1, 4):
            try:
                body = self._request_body(request)
                content = _extract_content(body)
                raw_profile = _extract_profile_object(content)
                try:
                    return VisualProfile.from_dict(raw_profile, platform)
                except (TypeError, ValueError):
                    raise _ValidationFailure("profile") from None
            except _ValidationFailure as exc:
                last_category = exc.category
        raise ArkVisionError(
            f"Ark response invalid after 3 attempts; last validation category: {last_category}",
        )

    def _request_body(self, request: urllib.request.Request) -> str | bytes:
        response = None
        try:
            response = self._opener(request, timeout=self._timeout)
            body = response.read()
            response_url = getattr(response, "geturl", None)
            if callable(response_url) and response_url() != ARK_CHAT_ENDPOINT:
                raise ArkVisionError("Ark response came from an unapproved endpoint")
            if not isinstance(body, (str, bytes)):
                raise _ValidationFailure("response")
            return body
        except _ValidationFailure:
            raise
        except ArkVisionError:
            raise
        except (TimeoutError, socket.timeout):
            raise ArkVisionError("Ark vision request timed out") from None
        except urllib.error.HTTPError as exc:
            status = exc.code
            close = getattr(exc, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            message = (
                f"Ark vision HTTP error (status {status})"
                if (
                    isinstance(status, int)
                    and not isinstance(status, bool)
                    and 100 <= status <= 599
                )
                else "Ark vision HTTP error"
            )
            raise ArkVisionError(message) from None
        except urllib.error.URLError as exc:
            close = getattr(exc, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ArkVisionError("Ark vision request timed out") from None
            raise ArkVisionError("Ark vision URL error") from None
        except Exception:
            raise ArkVisionError("Ark vision transport error") from None
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
