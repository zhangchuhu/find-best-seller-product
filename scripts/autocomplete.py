"""Strict, pure resolution of marketplace autocomplete evidence.

The caller supplies only suggestions visibly captured in the selected Chrome
session.  This module validates that evidence and chooses one displayed string
per prepared seed; it never drives a browser, calls a service, or invents text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from .ark_vision import VisualProfile
from .platforms import Platform
from .query_terms import contains_color_or_size


_ROOT_KEYS: Final = frozenset({"task_record_id", "platform", "seeds"})
_BLOCK_KEYS: Final = frozenset({"seed", "suggestions"})
_SUGGESTION_KEYS: Final = frozenset({"rank", "text"})
_WORD_RE: Final = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_RECORD_ID_LENGTH: Final = 128
_MAX_SUGGESTION_LENGTH: Final = 160
_ENGLISH_APPAREL_CATEGORY_WORDS: Final = frozenset({
    "dress", "dresses", "skirt", "skirts", "top", "tops", "blouse",
    "blouses", "shirt", "shirts", "pants", "trouser", "trousers",
    "jacket", "jackets", "coat", "coats", "shoe", "shoes", "jumpsuit",
    "jumpsuits", "romper", "rompers", "overall", "overalls", "bodysuit",
    "bodysuits", "swimsuit", "swimsuits", "bikini", "bikinis", "bra",
    "bras", "bralette", "bralettes", "hoodie", "hoodies", "sweater",
    "sweaters", "cardigan", "cardigans", "jean", "jeans", "short",
    "shorts", "legging", "leggings", "blazer", "blazers", "vest", "vests",
    "sandal", "sandals", "boot", "boots", "heel", "heels", "sneaker",
    "sneakers",
})
_MEXICAN_SPANISH_APPAREL_CATEGORY_WORDS: Final = frozenset({
    "vestido", "vestidos", "falda", "faldas", "top", "tops", "blusa",
    "blusas", "camisa", "camisas", "pantalon", "pantalones", "chaqueta",
    "chaquetas", "abrigo", "abrigos", "zapato", "zapatos", "mono", "monos",
    "enterizo", "enterizos", "mameluco", "mamelucos", "peto", "petos",
    "overol", "overoles", "body", "bodies", "traje", "trajes", "bikini",
    "bikinis", "sujetador", "sujetadores", "brasier", "brasiers", "sudadera",
    "sudaderas", "sueter", "sueteres", "cardigan", "cardigans", "jean",
    "jeans", "short", "shorts", "legging", "leggings", "blazer", "blazers",
    "chaleco", "chalecos", "sandalia", "sandalias", "bota", "botas", "tacon",
    "tacones", "tenis",
})
_APPAREL_CATEGORY_WORDS_BY_LANGUAGE: Final = {
    "en-US": _ENGLISH_APPAREL_CATEGORY_WORDS,
    "es-MX": _MEXICAN_SPANISH_APPAREL_CATEGORY_WORDS,
}
_SHARED_MARKET_APPAREL_WORDS: Final = frozenset({
    "top", "tops", "bikini", "bikinis", "cardigan", "cardigans", "jean",
    "jeans", "short", "shorts", "legging", "leggings", "blazer", "blazers",
    "body", "bodies",
})
_UNAMBIGUOUS_FOREIGN_APPAREL_WORDS: Final = frozenset({
    # German
    "kleid", "kleider", "rock", "roecke", "bluse", "blusen", "hemd",
    "hemden", "hose", "hosen", "jacke", "jacken", "mantel", "maentel",
    "schuh", "schuhe",
    # French (exclude "robe": it is also a valid English apparel word)
    "jupe", "jupes", "veste", "vestes", "manteau", "manteaux", "chaussure",
    "chaussures", "combinaison", "combinaisons",
    # Italian
    "abito", "abiti", "gonna", "gonne", "camicia", "camicie", "pantaloni",
    "giacca", "giacche", "cappotto", "cappotti", "scarpa", "scarpe", "tuta",
    "tute",
    # Portuguese (exclude shared Spanish words such as vestido/blusa/camisa)
    "saia", "saias", "calca", "calcas", "jaqueta", "jaquetas", "casaco",
    "casacos", "sapato", "sapatos", "macacao", "macacoes",
})
_WRONG_LANGUAGE_TERMS: Final = {
    # This is a bounded market-vocabulary guard, not general language detection:
    # valid English apparel words such as "robe" remain eligible on SHEIN US.
    "es-MX": (_ENGLISH_APPAREL_CATEGORY_WORDS - _SHARED_MARKET_APPAREL_WORDS)
    | _UNAMBIGUOUS_FOREIGN_APPAREL_WORDS,
    "en-US": (_MEXICAN_SPANISH_APPAREL_CATEGORY_WORDS - _SHARED_MARKET_APPAREL_WORDS)
    | _UNAMBIGUOUS_FOREIGN_APPAREL_WORDS,
}
@dataclass(frozen=True)
class Suggestion:
    rank: int
    text: str


@dataclass(frozen=True)
class SuggestionBlock:
    seed: str
    suggestions: tuple[Suggestion, ...]


@dataclass(frozen=True)
class ResolvedQueries:
    evidence: dict[str, object]
    queries: tuple[str, str, str]


def _trimmed_string(value: object, label: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    text = value.strip()
    if not text or len(text) > maximum:
        raise ValueError(f"{label} is outside its allowed length")
    return text


def _require_exact_object(value: object, expected: frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    if set(value) != expected:
        raise ValueError(f"{label} has an invalid shape")
    return value


def _comparison_words(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return set(_WORD_RE.findall(normalized))


def _duplicate_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _profile_words(values: tuple[str, ...]) -> set[str]:
    result: set[str] = set()
    for value in values:
        result.update(_comparison_words(value))
    return result


def _parse_evidence(
    raw: object,
    *,
    record_id: str,
    platform: Platform,
    profile: VisualProfile,
) -> tuple[dict[str, object], tuple[SuggestionBlock, SuggestionBlock, SuggestionBlock]]:
    if not isinstance(record_id, str) or not record_id.strip() or len(record_id.strip()) > _MAX_RECORD_ID_LENGTH:
        raise ValueError("record_id is invalid")
    if not isinstance(platform, Platform):
        raise TypeError("platform must be a Platform")
    if not isinstance(profile, VisualProfile):
        raise TypeError("profile must be a VisualProfile")

    root = _require_exact_object(raw, _ROOT_KEYS, "autocomplete evidence")
    evidence_record_id = _trimmed_string(root["task_record_id"], "task_record_id", maximum=_MAX_RECORD_ID_LENGTH)
    evidence_platform = _trimmed_string(root["platform"], "platform", maximum=64)
    if evidence_record_id != record_id.strip() or evidence_platform != platform.value:
        raise ValueError("autocomplete evidence does not bind to the requested task")
    if not isinstance(root["seeds"], list) or len(root["seeds"]) != 3:
        raise ValueError("autocomplete evidence must contain exactly three seed blocks")

    normalized_blocks: list[dict[str, object]] = []
    blocks: list[SuggestionBlock] = []
    for index, raw_block in enumerate(root["seeds"]):
        block = _require_exact_object(raw_block, _BLOCK_KEYS, "seed block")
        seed = _trimmed_string(block["seed"], "seed", maximum=120)
        if seed != profile.query_seeds[index]:
            raise ValueError("autocomplete seed does not match prepared profile")
        raw_suggestions = block["suggestions"]
        if not isinstance(raw_suggestions, list) or not 1 <= len(raw_suggestions) <= 10:
            raise ValueError("seed suggestions must contain 1-10 visible entries")

        suggestions: list[Suggestion] = []
        normalized_suggestions: list[dict[str, object]] = []
        seen: set[str] = set()
        for rank, raw_suggestion in enumerate(raw_suggestions, start=1):
            suggestion = _require_exact_object(raw_suggestion, _SUGGESTION_KEYS, "suggestion")
            supplied_rank = suggestion["rank"]
            if isinstance(supplied_rank, bool) or not isinstance(supplied_rank, int):
                raise TypeError("suggestion rank must be an integer")
            if supplied_rank != rank:
                raise ValueError("suggestion ranks must be visible contiguous order")
            text = _trimmed_string(suggestion["text"], "suggestion text", maximum=_MAX_SUGGESTION_LENGTH)
            duplicate_key = _duplicate_key(text)
            if duplicate_key in seen:
                raise ValueError("seed suggestions must be normalized-distinct")
            seen.add(duplicate_key)
            suggestions.append(Suggestion(rank=supplied_rank, text=text))
            normalized_suggestions.append({"rank": supplied_rank, "text": text})
        blocks.append(SuggestionBlock(seed=seed, suggestions=tuple(suggestions)))
        normalized_blocks.append({"seed": seed, "suggestions": normalized_suggestions})

    normalized_evidence: dict[str, object] = {
        "task_record_id": evidence_record_id,
        "platform": evidence_platform,
        "seeds": normalized_blocks,
    }
    return normalized_evidence, (blocks[0], blocks[1], blocks[2])


def _valid_suggestion(
    text: str,
    *,
    index: int,
    profile: VisualProfile,
    selected: set[str],
) -> bool:
    if contains_color_or_size(text, profile.query_language, profile.color):
        return False
    if _duplicate_key(text) in selected:
        return False

    words = _comparison_words(text)
    category_words = _comparison_words(profile.category) | _comparison_words(profile.subtype)
    marketplace_categories = _APPAREL_CATEGORY_WORDS_BY_LANGUAGE[profile.query_language]
    target_categories = category_words & marketplace_categories
    if not words & category_words:
        return False
    if words & _WRONG_LANGUAGE_TERMS[profile.query_language]:
        return False
    if (words & marketplace_categories) - target_categories:
        return False

    if index == 0:
        return words <= category_words
    if index == 1:
        silhouette_words = _comparison_words(profile.silhouette)
        construction_words = _profile_words(profile.construction)
        selling_point_words = _profile_words(profile.selling_points)
        return bool(words & (silhouette_words | construction_words | selling_point_words))

    use_scene_words = _comparison_words(profile.use_scene)
    profile_style_words = _profile_words(profile.style)
    return bool(words & use_scene_words or words & profile_style_words)


def resolve_autocomplete(
    raw: object,
    *,
    record_id: str,
    platform: Platform,
    profile: VisualProfile,
) -> ResolvedQueries:
    """Validate visible autocomplete evidence and select three verbatim queries.

    Suggestions are considered only in their captured visible rank order.  The
    returned evidence is a fresh normalized JSON-compatible tree, while a
    selected query retains its captured spelling and internal spacing.
    """
    evidence, blocks = _parse_evidence(
        raw,
        record_id=record_id,
        platform=platform,
        profile=profile,
    )
    selected: list[str] = []
    selected_keys: set[str] = set()
    for index, block in enumerate(blocks):
        chosen = next(
            (
                suggestion.text
                for suggestion in block.suggestions
                if _valid_suggestion(
                    suggestion.text,
                    index=index,
                    profile=profile,
                    selected=selected_keys,
                )
            ),
            None,
        )
        if chosen is None:
            raise ValueError("no visible autocomplete suggestion qualifies for its seed")
        selected.append(chosen)
        selected_keys.add(_duplicate_key(chosen))

    return ResolvedQueries(evidence=evidence, queries=(selected[0], selected[1], selected[2]))
