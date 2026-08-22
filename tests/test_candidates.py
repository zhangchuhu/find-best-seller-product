import unittest
from dataclasses import replace

from scripts.candidates import (
    MergedObservation,
    eligible_identities,
    merge_observations,
    select_results,
    validate_verified_candidate,
    visual_feature_text,
)
from scripts.models import Observation, Task, VerifiedCandidate
from scripts.platforms import Platform, UnsupportedPlatformError


def observation(
    query,
    rank,
    is_ad,
    title,
    url,
    product_id=None,
    thumbnail_url=None,
):
    return Observation(
        query=query,
        rank=rank,
        is_ad=is_ad,
        title=title,
        url=url,
        product_id=product_id,
        thumbnail_url=thumbnail_url,
    )


class CandidateMergeTests(unittest.TestCase):
    def test_tracking_variants_merge_to_literal_identity_and_distinct_query_hits(self):
        observations = [
            observation(
                "q1", 3, False, "Vestido uno",
                "https://www.mercadolibre.com.mx/vestido-MLM123?utm_source=one",
                "MLM123",
            ),
            observation(
                "q2", 8, True, "Vestido dos",
                "https://www.mercadolibre.com.mx/vestido-MLM123?fbclid=two",
                "MLM-123",
            ),
        ]

        merged = merge_observations(Platform.MERCADO_MX, observations)

        self.assertEqual(["mercado-libre-mx:MLM123"], list(merged))
        item = merged["mercado-libre-mx:MLM123"]
        self.assertEqual(frozenset({"q1", "q2"}), item.query_hits)
        self.assertEqual("https://www.mercadolibre.com.mx/vestido-MLM123", item.canonical_url)
        self.assertEqual(3, item.earliest_organic_rank)
        self.assertEqual(8, item.earliest_ad_rank)

    def test_repeated_cards_in_one_query_count_as_one_hit(self):
        merged = merge_observations(
            Platform.MERCADO_MX,
            [
                observation("q1", 4, False, "First", "https://www.mercadolibre.com.mx/item-MLM123", "MLM123"),
                observation("q1", 1, True, "Repeat", "https://www.mercadolibre.com.mx/item-MLM123", "MLM123"),
            ],
        )

        self.assertEqual(frozenset({"q1"}), merged["mercado-libre-mx:MLM123"].query_hits)
        self.assertEqual(set(), eligible_identities(merged))
        self.assertEqual({"mercado-libre-mx:MLM123"}, eligible_identities(merged, minimum_query_hits=1))

    def test_organic_sponsored_and_unknown_ranks_are_kept_separate(self):
        merged = merge_observations(
            Platform.SHEIN_US,
            [
                observation("q1", 7, None, "Unknown", "https://us.shein.com/dress-p-77.html"),
                observation("q2", 5, True, "Ad", "https://us.shein.com/dress-p-77.html"),
                observation("q3", 9, False, "Organic late", "https://us.shein.com/dress-p-77.html"),
                observation("q4", 2, False, "Organic early", "https://us.shein.com/dress-p-77.html"),
            ],
        )

        item = merged["shein-us:77"]
        self.assertEqual(2, item.earliest_organic_rank)
        self.assertEqual(5, item.earliest_ad_rank)

        unknown_only = merge_observations(
            Platform.SHEIN_US,
            [observation("q5", 1, None, "Unknown", "https://us.shein.com/top-p-88.html")],
        )["shein-us:88"]
        self.assertIsNone(unknown_only.earliest_organic_rank)
        self.assertIsNone(unknown_only.earliest_ad_rank)

    def test_representative_and_sorted_output_do_not_depend_on_input_order(self):
        values = [
            observation(" z ", 1, False, "Z title", "https://us.shein.com/z-p-20.html?utm_source=x", "20", " https://img/z "),
            observation("a", 1, False, "A title", "https://us.shein.com/a-p-20.html", "20", "https://img/a"),
            observation("q", 1, True, "Other", "https://us.shein.com/other-p-10.html", "10"),
        ]

        forward = merge_observations(Platform.SHEIN_US, values)
        reverse = merge_observations(Platform.SHEIN_US, reversed(values))

        self.assertEqual(forward, reverse)
        self.assertEqual(["shein-us:10", "shein-us:20"], list(forward))
        representative = forward["shein-us:20"]
        self.assertEqual("A title", representative.title)
        self.assertEqual("https://us.shein.com/a-p-20.html", representative.canonical_url)
        self.assertEqual("20", representative.product_id)
        self.assertEqual("https://img/a", representative.thumbnail_url)
        self.assertEqual(frozenset({"a", "z"}), representative.query_hits)

    def test_cross_platform_urls_and_wrong_id_grammar_are_rejected(self):
        with self.assertRaises(UnsupportedPlatformError):
            merge_observations(
                Platform.MERCADO_MX,
                [observation("q", 1, False, "Wrong host", "https://us.shein.com/dress-p-123.html", "MLM123")],
            )
        with self.assertRaises(ValueError):
            merge_observations(
                Platform.MERCADO_MX,
                [observation("q", 1, False, "Wrong ID", "https://www.mercadolibre.com.mx/item-MLM123", "123")],
            )

    def test_minimum_query_hits_must_be_a_positive_integer(self):
        for invalid in (0, -1, 1.5, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    eligible_identities({}, invalid)

    def test_merged_observation_normalizes_and_deep_freezes_direct_construction(self):
        item = MergedObservation(
            identity=" shein-us:123 ",
            platform="shein-us",
            title=" Dress ",
            canonical_url=" https://us.shein.com/dress-p-123.html ",
            product_id=" 123 ",
            query_hits={" q2 ", "q1"},
            earliest_organic_rank=2,
            earliest_ad_rank=None,
            thumbnail_url=" https://img/1 ",
        )

        self.assertEqual("shein-us:123", item.identity)
        self.assertIs(Platform.SHEIN_US, item.platform)
        self.assertEqual(frozenset({"q1", "q2"}), item.query_hits)
        self.assertEqual("123", item.product_id)
        self.assertEqual("https://img/1", item.thumbnail_url)
        with self.assertRaises(AttributeError):
            item.title = "Changed"
        with self.assertRaises(AttributeError):
            item.query_hits.add("q3")

    def test_merged_observation_rejects_invalid_direct_values(self):
        base = dict(
            identity="shein-us:123",
            platform=Platform.SHEIN_US,
            title="Dress",
            canonical_url="https://us.shein.com/dress-p-123.html",
            product_id="123",
            query_hits={"q1"},
            earliest_organic_rank=1,
            earliest_ad_rank=None,
            thumbnail_url=None,
        )
        for field, value in (("query_hits", set()), ("earliest_organic_rank", 0), ("platform", "other")):
            with self.subTest(field=field):
                invalid = dict(base)
                invalid[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    MergedObservation(**invalid)


def task(
    platform=Platform.MERCADO_MX,
    min_sold=100,
    min_reviews=10,
    min_rating=4.5,
    result_limit=15,
):
    return Task(
        record_id="rec1",
        sku="sku1",
        image_token="token1",
        image_name="image.jpg",
        platform=platform,
        min_sold=min_sold,
        min_reviews=min_reviews,
        min_rating=min_rating,
        result_limit=result_limit,
    )


def candidate(
    identity="mercado-libre-mx:MLM1",
    platform=Platform.MERCADO_MX,
    title="Candidate",
    canonical_url="https://www.mercadolibre.com.mx/item-MLM1",
    query_hits=frozenset({"q1", "q2"}),
    earliest_organic_rank=1,
    earliest_ad_rank=None,
    sold_value=100,
    reviews_value=10,
    rating_value=4.5,
    match_level="高度相似",
    visual_features=("A字短裙", "方领", "泡泡袖"),
):
    return VerifiedCandidate(
        identity=identity,
        platform=platform,
        title=title,
        canonical_url=canonical_url,
        query_hits=query_hits,
        earliest_organic_rank=earliest_organic_rank,
        earliest_ad_rank=earliest_ad_rank,
        sold_display=str(sold_value),
        sold_value=sold_value,
        reviews_display=str(reviews_value),
        reviews_value=reviews_value,
        rating_display=str(rating_value),
        rating_value=rating_value,
        match_level=match_level,
        visual_features=visual_features,
    )


class CandidateRankingTests(unittest.TestCase):
    def test_equality_at_every_threshold_is_inclusive(self):
        self.assertEqual([], validate_verified_candidate(task(), candidate()))

    def test_all_metric_and_query_failures_have_exact_ordered_codes(self):
        value = candidate(
            query_hits=frozenset({"q1"}),
            sold_value=99,
            reviews_value=9,
            rating_value=4.4,
        )

        self.assertEqual(
            [
                "insufficient_query_hits",
                "sold_below_threshold",
                "reviews_below_threshold",
                "rating_below_threshold",
            ],
            validate_verified_candidate(task(), value),
        )

    def test_platform_mismatch_combines_with_other_failures_deterministically(self):
        value = candidate(
            platform=Platform.SHEIN_US,
            query_hits=frozenset({"q1"}),
            sold_value=0,
            reviews_value=0,
            rating_value=0,
        )

        self.assertEqual(
            [
                "platform_mismatch",
                "insufficient_query_hits",
                "sold_below_threshold",
                "reviews_below_threshold",
                "rating_below_threshold",
            ],
            validate_verified_candidate(task(), value),
        )

    def test_marketplace_color_and_size_features_are_rejected_before_selection(self):
        """Catch candidate selection that ranks low-value feature claims."""
        cases = (
            (Platform.SHEIN_US, "red embroidery"),
            (Platform.SHEIN_US, "black bow"),
            (Platform.SHEIN_US, "rojo bordado"),
            (Platform.SHEIN_US, "unitalla"),
            (Platform.MERCADO_MX, "rojo bordado"),
            (Platform.MERCADO_MX, "talla M"),
            (Platform.MERCADO_MX, "red embroidery"),
            (Platform.MERCADO_MX, "one size"),
            (Platform.SHEIN_US, "plus size"),
            (Platform.SHEIN_US, "petite"),
            (Platform.SHEIN_US, "tall"),
            (Platform.SHEIN_US, "re\u200bd embroidery"),
            (Platform.MERCADO_MX, "r\u200bedo bordado"),
            (Platform.SHEIN_US, "pl\u200bus size"),
        )
        for platform, feature in cases:
            with self.subTest(platform=platform, feature=feature):
                value = candidate(platform=platform, visual_features=(feature,))
                current_task = task(platform=platform)
                self.assertEqual(
                    ["visual_features_contain_color_or_size"],
                    validate_verified_candidate(current_task, value),
                )
                self.assertEqual([], select_results(current_task, [value]))

    def test_valid_feature_text_is_formatting_only(self):
        english = candidate(visual_features=("3D flower applique", "puff sleeves", "A-line skirt"))
        spanish = candidate(
            platform=Platform.MERCADO_MX,
            visual_features=("aplique floral 3D", "manga abullonada", "falda línea A"),
        )
        self.assertEqual([], validate_verified_candidate(task(platform=Platform.MERCADO_MX), spanish))
        self.assertEqual([], validate_verified_candidate(task(platform=Platform.MERCADO_MX), english))
        self.assertEqual(
            [english.identity],
            [item.identity for item in select_results(task(platform=Platform.MERCADO_MX), [english])],
        )

    def test_query_hit_count_beats_match_level(self):
        three_hits = candidate(
            identity="mercado-libre-mx:MLM3",
            canonical_url="https://www.mercadolibre.com.mx/item-MLM3",
            query_hits=frozenset({"q1", "q2", "q3"}),
            match_level="类似竞品",
        )
        two_hits = candidate(match_level="同款")

        self.assertEqual(
            ["mercado-libre-mx:MLM3", "mercado-libre-mx:MLM1"],
            [item.identity for item in select_results(task(), [two_hits, three_hits])],
        )

    def test_match_level_beats_organic_rank(self):
        same = candidate(earliest_organic_rank=99, match_level="同款")
        similar = candidate(
            identity="mercado-libre-mx:MLM2",
            canonical_url="https://www.mercadolibre.com.mx/item-MLM2",
            earliest_organic_rank=1,
            match_level="高度相似",
        )

        self.assertEqual(
            ["mercado-libre-mx:MLM1", "mercado-libre-mx:MLM2"],
            [item.identity for item in select_results(task(), [similar, same])],
        )

    def test_organic_evidence_beats_equivalent_ad_only_evidence(self):
        organic = candidate(earliest_organic_rank=10, earliest_ad_rank=None)
        ad_only = candidate(
            identity="mercado-libre-mx:MLM2",
            canonical_url="https://www.mercadolibre.com.mx/item-MLM2",
            earliest_organic_rank=None,
            earliest_ad_rank=1,
        )

        self.assertEqual(
            ["mercado-libre-mx:MLM1", "mercado-libre-mx:MLM2"],
            [item.identity for item in select_results(task(), [ad_only, organic])],
        )

    def test_sold_reviews_rating_and_url_break_ties_in_order(self):
        base = candidate()
        cases = [
            (
                candidate(identity="mercado-libre-mx:MLM2", canonical_url="https://www.mercadolibre.com.mx/item-MLM2", sold_value=101),
                base,
                "mercado-libre-mx:MLM2",
            ),
            (
                candidate(identity="mercado-libre-mx:MLM2", canonical_url="https://www.mercadolibre.com.mx/item-MLM2", reviews_value=11),
                base,
                "mercado-libre-mx:MLM2",
            ),
            (
                candidate(identity="mercado-libre-mx:MLM2", canonical_url="https://www.mercadolibre.com.mx/item-MLM2", rating_value=4.6),
                base,
                "mercado-libre-mx:MLM2",
            ),
            (
                candidate(identity="mercado-libre-mx:MLM0", canonical_url="https://www.mercadolibre.com.mx/a-MLM0"),
                base,
                "mercado-libre-mx:MLM0",
            ),
        ]
        for left, right, expected_first in cases:
            with self.subTest(expected_first=expected_first):
                actual = select_results(task(), [right, left])
                self.assertEqual(expected_first, actual[0].identity)

    def test_duplicate_identity_keeps_best_representation_and_title_breaks_equal_keys(self):
        weaker = candidate(title="Z title", query_hits=frozenset({"q1", "q2"}))
        stronger = candidate(title="Z best", query_hits=frozenset({"q1", "q2", "q3"}))
        equal_but_lexical = candidate(title="A deterministic title", query_hits=frozenset({"q1", "q2", "q3"}))

        actual = select_results(task(), [weaker, stronger, equal_but_lexical])

        self.assertEqual(1, len(actual))
        self.assertEqual("A deterministic title", actual[0].title)

    def test_equal_title_duplicates_choose_same_complete_representation_when_reversed(self):
        base = candidate(title="Same title", earliest_ad_rank=7)
        cases = [
            (
                replace(base, query_hits=frozenset({"a1", "a2"})),
                replace(base, query_hits=frozenset({"z1", "z2"})),
            ),
            (replace(base, earliest_ad_rank=2), replace(base, earliest_ad_rank=9)),
            (replace(base, sold_display="100 A"), replace(base, sold_display="100 Z")),
            (replace(base, reviews_display="10 A"), replace(base, reviews_display="10 Z")),
            (replace(base, rating_display="4.5 A"), replace(base, rating_display="4.5 Z")),
        ]

        for lexically_first, lexically_later in cases:
            with self.subTest(first=lexically_first):
                forward = select_results(task(), [lexically_first, lexically_later])
                reverse = select_results(task(), [lexically_later, lexically_first])
                self.assertEqual([lexically_first], forward)
                self.assertEqual(forward, reverse)

    def test_invalid_candidates_are_rejected_without_weakening_thresholds(self):
        below = candidate(identity="mercado-libre-mx:MLM2", sold_value=99)
        passing = candidate()

        self.assertEqual(["mercado-libre-mx:MLM1"], [item.identity for item in select_results(task(), [below, passing])])

    def test_result_limit_returns_exactly_two_best_items(self):
        values = [
            candidate(identity="mercado-libre-mx:MLM3", canonical_url="https://www.mercadolibre.com.mx/item-MLM3", sold_value=101),
            candidate(identity="mercado-libre-mx:MLM2", canonical_url="https://www.mercadolibre.com.mx/item-MLM2", sold_value=102),
            candidate(identity="mercado-libre-mx:MLM1", canonical_url="https://www.mercadolibre.com.mx/item-MLM1", sold_value=103),
        ]

        actual = select_results(task(result_limit=2), values)

        self.assertEqual(2, len(actual))
        self.assertEqual(["mercado-libre-mx:MLM1", "mercado-libre-mx:MLM2"], [item.identity for item in actual])

    def test_visual_feature_text_preserves_validated_feature_order(self):
        self.assertEqual("高度相似｜A字短裙；方领；泡泡袖", visual_feature_text(candidate()))


if __name__ == "__main__":
    unittest.main()
