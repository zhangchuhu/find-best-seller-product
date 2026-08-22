import copy
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from scripts.ark_vision import VisualProfile
from scripts.autocomplete import (
    ResolvedQueries,
    Suggestion,
    SuggestionBlock,
    resolve_autocomplete,
)
from scripts.platforms import Platform


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def shein_profile() -> VisualProfile:
    return VisualProfile.from_dict({
        "category": "dress",
        "subtype": "mini dress",
        "silhouette": "A-line",
        "fit": "regular",
        "color": "black",
        "style": ["romantic", "elegant"],
        "selling_points": ["puff sleeve", "square neckline", "bow detail"],
        "construction": ["puff sleeve", "square neckline"],
        "defining_features": ["bow detail", "tiered skirt"],
        "exclusions": ["maxi length"],
        "use_scene": "cocktail",
        "query_language": "en-US",
        "query_seeds": ["mini dress", "puff sleeve mini dress", "cocktail dress"],
    }, Platform.SHEIN_US)


def mercado_profile() -> VisualProfile:
    return VisualProfile.from_dict({
        "category": "vestido",
        "subtype": "vestido corto",
        "silhouette": "entallado",
        "fit": "regular",
        "color": "negro",
        "style": ["romántico", "elegante"],
        "selling_points": ["manga abullonada", "escote cuadrado", "lazo"],
        "construction": ["manga abullonada", "escote cuadrado"],
        "defining_features": ["lazo", "falda en capas"],
        "exclusions": ["largo maxi"],
        "use_scene": "cóctel",
        "query_language": "es-MX",
        "query_seeds": ["vestido", "vestido manga abullonada", "vestido cóctel"],
    }, Platform.MERCADO_MX)


class AutocompleteResolverTests(unittest.TestCase):
    def resolve_shein(self, raw: object) -> ResolvedQueries:
        return resolve_autocomplete(
            raw,
            record_id="rec_source",
            platform=Platform.SHEIN_US,
            profile=shein_profile(),
        )

    def test_resolves_shein_fixture_to_first_qualifying_visible_suggestions(self) -> None:
        resolved = self.resolve_shein(fixture("shein-autocomplete.json"))
        self.assertEqual(
            ("mini dress", "puff sleeve mini dress", "cocktail dress"),
            resolved.queries,
        )
        self.assertEqual("shein-us", resolved.evidence["platform"])

    def test_resolves_mercado_fixture_with_accented_visible_text(self) -> None:
        resolved = resolve_autocomplete(
            fixture("mercado-autocomplete.json"),
            record_id="rec_source",
            platform=Platform.MERCADO_MX,
            profile=mercado_profile(),
        )
        self.assertEqual(
            ("vestido", "vestido manga abullonada", "vestido cóctel"),
            resolved.queries,
        )

    def test_rank_one_low_value_term_is_skipped_and_rank_two_is_returned_verbatim(self) -> None:
        for forbidden in ("black mini dress", "size 12 mini dress"):
            with self.subTest(forbidden=forbidden):
                raw = fixture("shein-autocomplete.json")
                raw["seeds"][0]["suggestions"] = [
                    {"rank": 1, "text": forbidden},
                    {"rank": 2, "text": "  Mini  Dress  "},
                ]
                resolved = self.resolve_shein(raw)
                self.assertEqual("Mini  Dress", resolved.queries[0])

    def test_source_color_metadata_is_rejected_even_when_not_in_the_shared_palette(self) -> None:
        raw = fixture("shein-autocomplete.json")
        raw["seeds"][0]["suggestions"] = [
            {"rank": 1, "text": "aubergine mini dress"},
            {"rank": 2, "text": "mini dress"},
        ]
        profile = shein_profile()
        profile = VisualProfile(
            category=profile.category,
            subtype=profile.subtype,
            silhouette=profile.silhouette,
            fit=profile.fit,
            color="aubergine",
            style=profile.style,
            selling_points=profile.selling_points,
            construction=profile.construction,
            defining_features=profile.defining_features,
            exclusions=profile.exclusions,
            use_scene=profile.use_scene,
            query_language=profile.query_language,
            query_seeds=profile.query_seeds,
        )
        resolved = resolve_autocomplete(
            raw,
            record_id="rec_source",
            platform=Platform.SHEIN_US,
            profile=profile,
        )
        self.assertEqual("mini dress", resolved.queries[0])

    def test_resolver_skips_low_value_terms_from_either_market_language(self) -> None:
        cases = (
            (
                Platform.SHEIN_US,
                shein_profile(),
                "shein-autocomplete.json",
                "rojo puff sleeve mini dress",
                "puff sleeve mini dress",
            ),
            (
                Platform.SHEIN_US,
                shein_profile(),
                "shein-autocomplete.json",
                "unitalla puff sleeve mini dress",
                "puff sleeve mini dress",
            ),
            (
                Platform.MERCADO_MX,
                mercado_profile(),
                "mercado-autocomplete.json",
                "vestido manga abullonada red",
                "vestido manga abullonada",
            ),
            (
                Platform.MERCADO_MX,
                mercado_profile(),
                "mercado-autocomplete.json",
                "vestido manga abullonada one size",
                "vestido manga abullonada",
            ),
        )
        for platform, profile, fixture_name, forbidden, expected in cases:
            with self.subTest(platform=platform.value, forbidden=forbidden):
                raw = fixture(fixture_name)
                raw["seeds"][1]["suggestions"] = [
                    {"rank": 1, "text": forbidden},
                    {"rank": 2, "text": expected},
                ]
                resolved = resolve_autocomplete(
                    raw,
                    record_id="rec_source",
                    platform=platform,
                    profile=profile,
                )
                self.assertEqual(expected, resolved.queries[1])

    def test_resolver_skips_each_meaningful_token_of_multiword_source_color(self) -> None:
        raw = fixture("shein-autocomplete.json")
        raw["seeds"][1]["suggestions"] = [
            {"rank": 1, "text": "apricot puff sleeve mini dress"},
            {"rank": 2, "text": "puff sleeve mini dress"},
        ]
        profile = shein_profile()
        profile = VisualProfile(
            category=profile.category,
            subtype=profile.subtype,
            silhouette=profile.silhouette,
            fit=profile.fit,
            color="apricot beige",
            style=profile.style,
            selling_points=profile.selling_points,
            construction=profile.construction,
            defining_features=profile.defining_features,
            exclusions=profile.exclusions,
            use_scene=profile.use_scene,
            query_language=profile.query_language,
            query_seeds=profile.query_seeds,
        )
        resolved = resolve_autocomplete(
            raw,
            record_id="rec_source",
            platform=Platform.SHEIN_US,
            profile=profile,
        )
        self.assertEqual("puff sleeve mini dress", resolved.queries[1])

    def test_skips_invalid_earlier_category_drift_without_failing_early(self) -> None:
        raw = fixture("shein-autocomplete.json")
        raw["seeds"][1]["suggestions"] = [
            {"rank": 1, "text": "puff sleeve mini skirt"},
            {"rank": 2, "text": "puff sleeve mini dress"},
        ]
        self.assertEqual("puff sleeve mini dress", self.resolve_shein(raw).queries[1])

    def test_skips_common_outer_garment_category_drift_without_rewriting_visible_text(self) -> None:
        raw = fixture("shein-autocomplete.json")
        raw["seeds"][2]["suggestions"] = [
            {"rank": 1, "text": "cocktail dress jumpsuit"},
            {"rank": 2, "text": "cocktail dress"},
        ]
        self.assertEqual("cocktail dress", self.resolve_shein(raw).queries[2])

    def test_plural_overalls_category_drift_is_skipped_for_a_dress_source(self) -> None:
        raw = fixture("shein-autocomplete.json")
        raw["seeds"][2]["suggestions"] = [
            {"rank": 1, "text": "cocktail dress overalls"},
            {"rank": 2, "text": "cocktail dress"},
        ]
        self.assertEqual("cocktail dress", self.resolve_shein(raw).queries[2])

    def test_mexican_overall_category_variants_are_skipped_for_a_dress_source(self) -> None:
        for category in ("peto", "overol"):
            with self.subTest(category=category):
                raw = fixture("mercado-autocomplete.json")
                raw["seeds"][2]["suggestions"] = [
                    {"rank": 1, "text": f"vestido cóctel {category}"},
                    {"rank": 2, "text": "vestido cóctel"},
                ]
                resolved = resolve_autocomplete(
                    raw,
                    record_id="rec_source",
                    platform=Platform.MERCADO_MX,
                    profile=mercado_profile(),
                )
                self.assertEqual("vestido cóctel", resolved.queries[2])

    def test_common_outer_garment_category_is_accepted_when_it_is_the_source_category(self) -> None:
        profile = VisualProfile.from_dict({
            "category": "jumpsuit",
            "subtype": "cocktail jumpsuit",
            "silhouette": "wide leg",
            "fit": "regular",
            "color": "black",
            "style": ["formal"],
            "selling_points": ["wide leg", "tie waist"],
            "construction": ["tie waist", "v neck"],
            "defining_features": ["belt detail", "pleated leg"],
            "exclusions": ["dress"],
            "use_scene": "cocktail",
            "query_language": "en-US",
            "query_seeds": ["jumpsuit", "wide leg jumpsuit", "cocktail jumpsuit"],
        }, Platform.SHEIN_US)
        raw = {
            "task_record_id": "rec_source",
            "platform": "shein-us",
            "seeds": [
                {"seed": "jumpsuit", "suggestions": [{"rank": 1, "text": "jumpsuit"}]},
                {"seed": "wide leg jumpsuit", "suggestions": [{"rank": 1, "text": "wide leg jumpsuit"}]},
                {"seed": "cocktail jumpsuit", "suggestions": [{"rank": 1, "text": "cocktail jumpsuit"}]},
            ],
        }
        resolved = resolve_autocomplete(
            raw,
            record_id="rec_source",
            platform=Platform.SHEIN_US,
            profile=profile,
        )
        self.assertEqual(("jumpsuit", "wide leg jumpsuit", "cocktail jumpsuit"), resolved.queries)

    def test_plural_overalls_category_is_accepted_when_it_is_the_source_category(self) -> None:
        profile = VisualProfile.from_dict({
            "category": "overalls",
            "subtype": "cocktail overalls",
            "silhouette": "wide leg",
            "fit": "regular",
            "color": "black",
            "style": ["formal"],
            "selling_points": ["wide leg", "bib front"],
            "construction": ["bib front", "tie waist"],
            "defining_features": ["belt detail", "pleated leg"],
            "exclusions": ["dress"],
            "use_scene": "cocktail",
            "query_language": "en-US",
            "query_seeds": ["overalls", "wide leg overalls", "cocktail overalls"],
        }, Platform.SHEIN_US)
        raw = {
            "task_record_id": "rec_source",
            "platform": "shein-us",
            "seeds": [
                {"seed": "overalls", "suggestions": [{"rank": 1, "text": "overalls"}]},
                {"seed": "wide leg overalls", "suggestions": [{"rank": 1, "text": "wide leg overalls"}]},
                {"seed": "cocktail overalls", "suggestions": [{"rank": 1, "text": "cocktail overalls"}]},
            ],
        }
        resolved = resolve_autocomplete(
            raw,
            record_id="rec_source",
            platform=Platform.SHEIN_US,
            profile=profile,
        )
        self.assertEqual(("overalls", "wide leg overalls", "cocktail overalls"), resolved.queries)

    def test_spanish_mono_category_drift_is_rejected(self) -> None:
        raw = fixture("mercado-autocomplete.json")
        raw["seeds"][2]["suggestions"] = [
            {"rank": 1, "text": "vestido cóctel mono"},
            {"rank": 2, "text": "vestido cóctel"},
        ]
        resolved = resolve_autocomplete(
            raw,
            record_id="rec_source",
            platform=Platform.MERCADO_MX,
            profile=mercado_profile(),
        )
        self.assertEqual("vestido cóctel", resolved.queries[2])

    def test_valid_english_apparel_word_is_not_treated_as_wrong_language(self) -> None:
        raw = fixture("shein-autocomplete.json")
        raw["seeds"][2]["suggestions"] = [{"rank": 1, "text": "cocktail dress robe"}]
        self.assertEqual("cocktail dress robe", self.resolve_shein(raw).queries[2])

    def test_unambiguous_foreign_apparel_words_are_skipped_in_each_market_language(self) -> None:
        cases = (
            (Platform.SHEIN_US, shein_profile(), "shein-autocomplete.json", "shein-us", "cocktail dress", "cocktail dress"),
            (Platform.MERCADO_MX, mercado_profile(), "mercado-autocomplete.json", "mercado-libre-mx", "vestido cóctel", "vestido cóctel"),
        )
        for platform, profile, name, platform_value, invalid_base, valid in cases:
            for foreign_category in ("kleid", "jupe", "abito", "saia"):
                with self.subTest(platform=platform.value, foreign_category=foreign_category):
                    raw = fixture(name)
                    raw["seeds"][2]["suggestions"] = [
                        {"rank": 1, "text": f"{invalid_base} {foreign_category}"},
                        {"rank": 2, "text": valid},
                    ]
                    resolved = resolve_autocomplete(
                        raw,
                        record_id="rec_source",
                        platform=platform,
                        profile=profile,
                    )
                    self.assertEqual(valid, resolved.queries[2])

    def test_rejects_exact_root_block_and_item_shapes(self) -> None:
        mutations = {
            "root unknown": lambda raw: raw.update({"extra": True}),
            "root missing": lambda raw: raw.pop("platform"),
            "block unknown": lambda raw: raw["seeds"][0].update({"extra": True}),
            "block missing": lambda raw: raw["seeds"][0].pop("seed"),
            "item unknown": lambda raw: raw["seeds"][0]["suggestions"][0].update({"extra": True}),
            "item missing": lambda raw: raw["seeds"][0]["suggestions"][0].pop("text"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                raw = fixture("shein-autocomplete.json")
                mutate(raw)
                with self.assertRaises((TypeError, ValueError)):
                    self.resolve_shein(raw)

    def test_rejects_non_list_containers_wrong_block_count_and_invalid_suggestion_counts(self) -> None:
        mutations = {
            "seeds tuple": lambda raw: raw.update({"seeds": tuple(raw["seeds"])}),
            "suggestions tuple": lambda raw: raw["seeds"][0].update({"suggestions": tuple(raw["seeds"][0]["suggestions"])}),
            "two blocks": lambda raw: raw["seeds"].pop(),
            "no suggestion": lambda raw: raw["seeds"][0].update({"suggestions": []}),
            "eleven suggestions": lambda raw: raw["seeds"][0].update({
                "suggestions": [
                    {"rank": rank, "text": f"mini dress {rank}"}
                    for rank in range(1, 12)
                ],
            }),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                raw = fixture("shein-autocomplete.json")
                mutate(raw)
                with self.assertRaises((TypeError, ValueError)):
                    self.resolve_shein(raw)

    def test_rejects_boolean_and_noncontiguous_ranks(self) -> None:
        for rank in (True, 0, 3):
            with self.subTest(rank=rank):
                raw = fixture("shein-autocomplete.json")
                raw["seeds"][0]["suggestions"][0]["rank"] = rank
                with self.assertRaises((TypeError, ValueError)):
                    self.resolve_shein(raw)

    def test_binds_record_platform_and_seed_order_to_the_prepared_profile(self) -> None:
        mutations = {
            "record": lambda raw: raw.update({"task_record_id": "rec_other"}),
            "platform": lambda raw: raw.update({"platform": "mercado-libre-mx"}),
            "seed order": lambda raw: raw["seeds"].__setitem__(0, raw["seeds"][1]),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                raw = fixture("shein-autocomplete.json")
                mutate(raw)
                with self.assertRaises((TypeError, ValueError)):
                    self.resolve_shein(raw)

    def test_rejects_empty_and_overlong_string_fields(self) -> None:
        cases = (
            ("empty record", lambda raw: raw.update({"task_record_id": "  "})),
            ("empty suggestion", lambda raw: raw["seeds"][0]["suggestions"][0].update({"text": "\t"})),
            ("overlong suggestion", lambda raw: raw["seeds"][0]["suggestions"][0].update({"text": "x" * 161})),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                raw = fixture("shein-autocomplete.json")
                mutate(raw)
                with self.assertRaises((TypeError, ValueError)):
                    self.resolve_shein(raw)

    def test_rejects_normalized_duplicates_within_visible_suggestions(self) -> None:
        raw = fixture("shein-autocomplete.json")
        raw["seeds"][0]["suggestions"] = [
            {"rank": 1, "text": "ｍini Dress"},
            {"rank": 2, "text": " mini   dress "},
        ]
        with self.assertRaises((TypeError, ValueError)):
            self.resolve_shein(raw)

    def test_rejects_wrong_language_and_role_missing_suggestions_when_no_alternative_exists(self) -> None:
        cases = {
            "wrong language": (0, "vestido corto"),
            "core role adds construction": (0, "puff sleeve mini dress"),
            "second role missing construction": (1, "mini dress"),
            "third role structural only": (2, "square neckline mini dress"),
        }
        for label, (index, text) in cases.items():
            with self.subTest(label=label):
                raw = fixture("shein-autocomplete.json")
                raw["seeds"][index]["suggestions"] = [{"rank": 1, "text": text}]
                with self.assertRaises((TypeError, ValueError)):
                    self.resolve_shein(raw)

    def test_rejects_cross_block_normalized_duplicate_when_no_distinct_suggestion_remains(self) -> None:
        raw = fixture("shein-autocomplete.json")
        raw["seeds"][1]["suggestions"] = [{"rank": 1, "text": " mini  dress "}]
        with self.assertRaises((TypeError, ValueError)):
            self.resolve_shein(raw)

    def test_copies_canonical_evidence_without_aliasing_mutable_input(self) -> None:
        raw = fixture("shein-autocomplete.json")
        resolved = self.resolve_shein(raw)
        raw["platform"] = "mercado-libre-mx"
        raw["seeds"][0]["suggestions"][0]["text"] = "changed"
        self.assertEqual("shein-us", resolved.evidence["platform"])
        self.assertEqual("mini dress", resolved.evidence["seeds"][0]["suggestions"][0]["text"])
        self.assertEqual(json.loads(json.dumps(resolved.evidence)), resolved.evidence)

    def test_public_dataclasses_are_frozen(self) -> None:
        suggestion = Suggestion(rank=1, text="mini dress")
        block = SuggestionBlock(seed="mini dress", suggestions=(suggestion,))
        resolved = ResolvedQueries(evidence={}, queries=("a", "b", "c"))
        for instance, field, value in (
            (suggestion, "rank", 2),
            (block, "seed", "other"),
            (resolved, "queries", ("x", "y", "z")),
        ):
            with self.subTest(instance=type(instance).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(instance, field, value)


if __name__ == "__main__":
    unittest.main()
