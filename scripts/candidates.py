"""Deterministic aggregation and selection of marketplace candidates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .models import Observation, Task, VerifiedCandidate
from .platforms import Platform, canonicalize_url, product_identity
from .query_terms import MARKETPLACE_LANGUAGES, contains_color_or_size


MATCH_ORDER = {"同款": 0, "高度相似": 1, "类似竞品": 2}


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _positive_rank(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 1:
        raise ValueError(f"{field} must be at least 1")
    return value


def _platform(value: object) -> Platform:
    if isinstance(value, Platform):
        return value
    if isinstance(value, str):
        try:
            return Platform(value)
        except ValueError as exc:
            raise ValueError("unsupported platform") from exc
    raise TypeError("platform must be a Platform or platform value")


def _query_hits(value: object) -> frozenset[str]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise TypeError("query_hits must be a sequence of strings")
    result = frozenset(_text(item, "query_hits") for item in value)
    if not result:
        raise ValueError("query_hits must not be empty")
    return result


@dataclass(frozen=True)
class MergedObservation:
    identity: str
    platform: Platform
    title: str
    canonical_url: str
    product_id: str | None
    query_hits: frozenset[str]
    earliest_organic_rank: int | None
    earliest_ad_rank: int | None
    thumbnail_url: str | None

    def __post_init__(self) -> None:
        organic = None if self.earliest_organic_rank is None else _positive_rank(
            self.earliest_organic_rank, "earliest_organic_rank"
        )
        ad = None if self.earliest_ad_rank is None else _positive_rank(
            self.earliest_ad_rank, "earliest_ad_rank"
        )
        object.__setattr__(self, "identity", _text(self.identity, "identity"))
        object.__setattr__(self, "platform", _platform(self.platform))
        object.__setattr__(self, "title", _text(self.title, "title"))
        object.__setattr__(self, "canonical_url", _text(self.canonical_url, "canonical_url"))
        object.__setattr__(self, "product_id", _optional_text(self.product_id, "product_id"))
        object.__setattr__(self, "query_hits", _query_hits(self.query_hits))
        object.__setattr__(self, "earliest_organic_rank", organic)
        object.__setattr__(self, "earliest_ad_rank", ad)
        object.__setattr__(self, "thumbnail_url", _optional_text(self.thumbnail_url, "thumbnail_url"))


def _representative_key(observation: Observation, canonical_url: str) -> tuple[object, ...]:
    evidence_order = False if observation.is_ad is False else True if observation.is_ad is True else None
    evidence_priority = {False: 0, True: 1, None: 2}[evidence_order]
    return (
        evidence_priority,
        observation.rank,
        observation.query,
        canonical_url,
        observation.title,
        observation.product_id or "",
        observation.thumbnail_url or "",
    )


def merge_observations(
    platform: Platform, observations: Iterable[Observation]
) -> dict[str, MergedObservation]:
    """Merge repeated search cards into stable, platform-scoped products."""
    platform = _platform(platform)
    grouped: dict[str, list[tuple[Observation, str]]] = {}
    for observation in observations:
        if not isinstance(observation, Observation):
            raise TypeError("observations must contain Observation values")
        canonical_url = canonicalize_url(platform, observation.url)
        identity = product_identity(platform, observation.url, observation.product_id)
        grouped.setdefault(identity, []).append((observation, canonical_url))

    result: dict[str, MergedObservation] = {}
    for identity in sorted(grouped):
        entries = grouped[identity]
        representative, representative_url = min(
            entries, key=lambda entry: _representative_key(entry[0], entry[1])
        )
        organic_ranks = [item.rank for item, _ in entries if item.is_ad is False]
        ad_ranks = [item.rank for item, _ in entries if item.is_ad is True]
        result[identity] = MergedObservation(
            identity=identity,
            platform=platform,
            title=representative.title,
            canonical_url=representative_url,
            product_id=representative.product_id,
            query_hits=frozenset(item.query for item, _ in entries),
            earliest_organic_rank=min(organic_ranks) if organic_ranks else None,
            earliest_ad_rank=min(ad_ranks) if ad_ranks else None,
            thumbnail_url=representative.thumbnail_url,
        )
    return result


def eligible_identities(
    merged: Mapping[str, MergedObservation], minimum_query_hits: int = 2
) -> set[str]:
    """Return identities observed through at least the required number of queries."""
    if isinstance(minimum_query_hits, bool) or not isinstance(minimum_query_hits, int):
        raise TypeError("minimum_query_hits must be an integer")
    if minimum_query_hits < 1:
        raise ValueError("minimum_query_hits must be at least 1")
    return {
        identity
        for identity, observation in merged.items()
        if len(observation.query_hits) >= minimum_query_hits
    }


def validate_verified_candidate(task: Task, candidate: VerifiedCandidate) -> list[str]:
    """Return all rejection reasons in a stable, machine-readable order."""
    reasons: list[str] = []
    if candidate.platform is not task.platform:
        reasons.append("platform_mismatch")
    if len(candidate.query_hits) < 2:
        reasons.append("insufficient_query_hits")
    if candidate.sold_value < task.min_sold:
        reasons.append("sold_below_threshold")
    if candidate.reviews_value < task.min_reviews:
        reasons.append("reviews_below_threshold")
    if candidate.rating_value < task.min_rating:
        reasons.append("rating_below_threshold")
    if any(
        contains_color_or_size(feature, language)
        for feature in candidate.visual_features
        for language in MARKETPLACE_LANGUAGES
    ):
        reasons.append("visual_features_contain_color_or_size")
    return reasons


def _ranking_key(candidate: VerifiedCandidate) -> tuple[object, ...]:
    has_no_organic_rank = candidate.earliest_organic_rank is None
    evidence_rank = (
        candidate.earliest_ad_rank
        if has_no_organic_rank
        else candidate.earliest_organic_rank
    )
    return (
        -len(candidate.query_hits),
        MATCH_ORDER[candidate.match_level],
        has_no_organic_rank,
        evidence_rank,
        -candidate.sold_value,
        -candidate.reviews_value,
        -candidate.rating_value,
        candidate.canonical_url,
    )


def _optional_rank_key(rank: int | None) -> tuple[bool, int]:
    return (rank is None, rank or 0)


def _duplicate_representation_key(candidate: VerifiedCandidate) -> tuple[object, ...]:
    """Deterministically choose equivalent marketplace evidence only."""
    return (
        candidate.title,
        tuple(sorted(candidate.query_hits)),
        _optional_rank_key(candidate.earliest_organic_rank),
        _optional_rank_key(candidate.earliest_ad_rank),
        candidate.sold_display,
        candidate.reviews_display,
        candidate.rating_display,
    )


def select_results(
    task: Task, candidates: Iterable[VerifiedCandidate]
) -> list[VerifiedCandidate]:
    """Filter, deduplicate, rank, and limit verified candidates."""
    by_identity: dict[str, VerifiedCandidate] = {}
    for candidate in candidates:
        if validate_verified_candidate(task, candidate):
            continue
        current = by_identity.get(candidate.identity)
        if current is None:
            by_identity[candidate.identity] = candidate
            continue
        candidate_key = _ranking_key(candidate)
        current_key = _ranking_key(current)
        if candidate_key < current_key or (
            candidate_key == current_key
            and _duplicate_representation_key(candidate)
            < _duplicate_representation_key(current)
        ):
            by_identity[candidate.identity] = candidate

    ranked = sorted(by_identity.values(), key=_ranking_key)
    return ranked[: task.result_limit]


def visual_feature_text(candidate: VerifiedCandidate) -> str:
    """Format a candidate's match level and ordered visual features."""
    return f"{candidate.match_level}｜{'；'.join(candidate.visual_features)}"
