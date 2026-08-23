"""Strict immutable domain models for product research."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import ClassVar

from .platforms import Platform


def _validated_raw(raw: Mapping[str, object], fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise TypeError("model input must be a mapping")
    supplied = frozenset(raw.keys())
    if supplied != fields:
        unknown = supplied - fields
        missing = fields - supplied
        details = []
        if unknown:
            details.append(f"unknown keys: {sorted(unknown)!r}")
        if missing:
            details.append(f"missing keys: {sorted(missing)!r}")
        raise ValueError("; ".join(details))
    return raw


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _integer(value: object, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return value


def _number(value: object, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _platform(value: object) -> Platform:
    if isinstance(value, Platform):
        return value
    if isinstance(value, str):
        try:
            return Platform(value)
        except ValueError as exc:
            raise ValueError("unsupported platform") from exc
    raise TypeError("platform must be a Platform or platform value")


def _strings(value: object, field: str) -> list[str]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise TypeError(f"{field} must be a sequence of strings")
    normalized = [_text(item, field) for item in value]
    return sorted(normalized) if isinstance(value, (set, frozenset)) else normalized


@dataclass(frozen=True)
class Task:
    record_id: str
    sku: str
    image_token: str
    image_name: str
    platform: Platform
    min_sold: int
    min_reviews: int
    min_rating: float
    result_limit: int

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"record_id", "sku", "image_token", "image_name", "platform", "min_sold", "min_reviews", "min_rating", "result_limit"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _text(self.record_id, "record_id"))
        object.__setattr__(self, "sku", _text(self.sku, "sku"))
        object.__setattr__(self, "image_token", _text(self.image_token, "image_token"))
        object.__setattr__(self, "image_name", _text(self.image_name, "image_name"))
        object.__setattr__(self, "platform", _platform(self.platform))
        object.__setattr__(self, "min_sold", _integer(self.min_sold, "min_sold", minimum=0))
        object.__setattr__(self, "min_reviews", _integer(self.min_reviews, "min_reviews", minimum=0))
        object.__setattr__(self, "min_rating", _number(self.min_rating, "min_rating", minimum=0, maximum=5))
        object.__setattr__(self, "result_limit", _integer(self.result_limit, "result_limit", minimum=1))

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Task:
        raw = _validated_raw(raw, cls._FIELDS)
        return cls(
            record_id=_text(raw["record_id"], "record_id"),
            sku=_text(raw["sku"], "sku"),
            image_token=_text(raw["image_token"], "image_token"),
            image_name=_text(raw["image_name"], "image_name"),
            platform=_platform(raw["platform"]),
            min_sold=_integer(raw["min_sold"], "min_sold", minimum=0),
            min_reviews=_integer(raw["min_reviews"], "min_reviews", minimum=0),
            min_rating=_number(raw["min_rating"], "min_rating", minimum=0, maximum=5),
            result_limit=_integer(raw["result_limit"], "result_limit", minimum=1),
        )


@dataclass(frozen=True)
class Observation:
    query: str
    rank: int
    is_ad: bool | None
    title: str
    url: str
    product_id: str | None
    thumbnail_url: str | None

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"query", "rank", "is_ad", "title", "url", "product_id", "thumbnail_url"}
    )

    def __post_init__(self) -> None:
        if self.is_ad is not None and type(self.is_ad) is not bool:
            raise TypeError("is_ad must be True, False, or None")
        object.__setattr__(self, "query", _text(self.query, "query"))
        object.__setattr__(self, "rank", _integer(self.rank, "rank", minimum=1))
        object.__setattr__(self, "title", _text(self.title, "title"))
        object.__setattr__(self, "url", _text(self.url, "url"))
        object.__setattr__(self, "product_id", _optional_text(self.product_id, "product_id"))
        object.__setattr__(self, "thumbnail_url", _optional_text(self.thumbnail_url, "thumbnail_url"))

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Observation:
        raw = _validated_raw(raw, cls._FIELDS)
        is_ad = raw["is_ad"]
        if is_ad is not None and type(is_ad) is not bool:
            raise TypeError("is_ad must be True, False, or None")
        return cls(
            query=_text(raw["query"], "query"),
            rank=_integer(raw["rank"], "rank", minimum=1),
            is_ad=is_ad,
            title=_text(raw["title"], "title"),
            url=_text(raw["url"], "url"),
            product_id=_optional_text(raw["product_id"], "product_id"),
            thumbnail_url=_optional_text(raw["thumbnail_url"], "thumbnail_url"),
        )


@dataclass(frozen=True)
class VerifiedCandidate:
    identity: str
    platform: Platform
    title: str
    canonical_url: str
    query_hits: frozenset[str]
    earliest_organic_rank: int | None
    earliest_ad_rank: int | None
    sold_display: str | None
    sold_value: int | None
    reviews_display: str | None
    reviews_value: int | None
    rating_display: str | None
    rating_value: float | None
    match_level: str
    visual_features: tuple[str, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "identity", "platform", "title", "canonical_url", "query_hits",
            "earliest_organic_rank", "earliest_ad_rank", "sold_display", "sold_value",
            "reviews_display", "reviews_value", "rating_display", "rating_value",
            "match_level", "visual_features",
        }
    )
    _MATCH_LEVELS: ClassVar[frozenset[str]] = frozenset({"同款", "高度相似", "类似竞品"})

    def __post_init__(self) -> None:
        query_hits = _strings(self.query_hits, "query_hits")
        if not query_hits:
            raise ValueError("query_hits must not be empty")
        visual_features = _strings(self.visual_features, "visual_features")
        if not visual_features:
            raise ValueError("visual_features must not be empty")
        if len(visual_features) != len(set(visual_features)):
            raise ValueError("visual_features must be distinct")

        organic = None if self.earliest_organic_rank is None else _integer(
            self.earliest_organic_rank, "earliest_organic_rank", minimum=1
        )
        ad = None if self.earliest_ad_rank is None else _integer(
            self.earliest_ad_rank, "earliest_ad_rank", minimum=1
        )
        if organic is None and ad is None:
            raise ValueError("at least one candidate rank is required")
        match_level = _text(self.match_level, "match_level")
        if match_level not in self._MATCH_LEVELS:
            raise ValueError("unsupported match_level")

        object.__setattr__(self, "identity", _text(self.identity, "identity"))
        object.__setattr__(self, "platform", _platform(self.platform))
        object.__setattr__(self, "title", _text(self.title, "title"))
        object.__setattr__(self, "canonical_url", _text(self.canonical_url, "canonical_url"))
        object.__setattr__(self, "query_hits", frozenset(query_hits))
        object.__setattr__(self, "earliest_organic_rank", organic)
        object.__setattr__(self, "earliest_ad_rank", ad)
        sold_display = _optional_text(self.sold_display, "sold_display")
        sold_value = None if self.sold_value is None else _integer(self.sold_value, "sold_value", minimum=0)
        reviews_display = _optional_text(self.reviews_display, "reviews_display")
        reviews_value = None if self.reviews_value is None else _integer(self.reviews_value, "reviews_value", minimum=0)
        rating_display = _optional_text(self.rating_display, "rating_display")
        rating_value = None if self.rating_value is None else _number(
            self.rating_value, "rating_value", minimum=0, maximum=5
        )
        for display, value, label in (
            (sold_display, sold_value, "sold"),
            (reviews_display, reviews_value, "reviews"),
            (rating_display, rating_value, "rating"),
        ):
            if display is None and value is not None:
                raise ValueError(f"{label} value requires a display")
        if self.platform is Platform.SHEIN_US and (
            reviews_display is None or reviews_value is None
            or rating_display is None or rating_value is None
        ):
            raise ValueError("SHEIN candidates require reviews and rating")
        if self.platform is Platform.MERCADO_MX and (sold_display is None or sold_value is None):
            raise ValueError("Mercado candidates require sold count")
        object.__setattr__(self, "sold_display", sold_display)
        object.__setattr__(self, "sold_value", sold_value)
        object.__setattr__(self, "reviews_display", reviews_display)
        object.__setattr__(self, "reviews_value", reviews_value)
        object.__setattr__(self, "rating_display", rating_display)
        object.__setattr__(self, "rating_value", rating_value)
        object.__setattr__(self, "match_level", match_level)
        object.__setattr__(self, "visual_features", tuple(visual_features))

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> VerifiedCandidate:
        raw = _validated_raw(raw, cls._FIELDS)
        query_hits = _strings(raw["query_hits"], "query_hits")
        if not query_hits:
            raise ValueError("query_hits must not be empty")
        visual_features = _strings(raw["visual_features"], "visual_features")
        if not visual_features:
            raise ValueError("visual_features must not be empty")
        if len(visual_features) != len(set(visual_features)):
            raise ValueError("visual_features must be distinct")

        organic_raw = raw["earliest_organic_rank"]
        ad_raw = raw["earliest_ad_rank"]
        organic = None if organic_raw is None else _integer(organic_raw, "earliest_organic_rank", minimum=1)
        ad = None if ad_raw is None else _integer(ad_raw, "earliest_ad_rank", minimum=1)
        if organic is None and ad is None:
            raise ValueError("at least one candidate rank is required")
        match_level = _text(raw["match_level"], "match_level")
        if match_level not in cls._MATCH_LEVELS:
            raise ValueError("unsupported match_level")

        return cls(
            identity=_text(raw["identity"], "identity"),
            platform=_platform(raw["platform"]),
            title=_text(raw["title"], "title"),
            canonical_url=_text(raw["canonical_url"], "canonical_url"),
            query_hits=frozenset(query_hits),
            earliest_organic_rank=organic,
            earliest_ad_rank=ad,
            sold_display=_optional_text(raw["sold_display"], "sold_display"),
            sold_value=None if raw["sold_value"] is None else _integer(raw["sold_value"], "sold_value", minimum=0),
            reviews_display=_optional_text(raw["reviews_display"], "reviews_display"),
            reviews_value=None if raw["reviews_value"] is None else _integer(raw["reviews_value"], "reviews_value", minimum=0),
            rating_display=_optional_text(raw["rating_display"], "rating_display"),
            rating_value=None if raw["rating_value"] is None else _number(raw["rating_value"], "rating_value", minimum=0, maximum=5),
            match_level=match_level,
            visual_features=tuple(visual_features),
        )
