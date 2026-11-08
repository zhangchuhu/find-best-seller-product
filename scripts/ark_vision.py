"""Read-only legacy visual profile validation for finalized checkpoints."""

from __future__ import annotations

import re
from collections.abc import Mapping as MappingABC, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .platforms import Platform
from .query_terms import contains_color_or_size


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
