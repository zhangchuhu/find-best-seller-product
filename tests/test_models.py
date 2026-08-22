import dataclasses
import unittest

from scripts.models import Observation, Task, VerifiedCandidate
from scripts.platforms import Platform


def valid_task() -> dict[str, object]:
    return {
        "record_id": " rec-1 ",
        "sku": " SKU-9 ",
        "image_token": " token ",
        "image_name": " dress.jpg ",
        "platform": "mercado-libre-mx",
        "min_sold": 0,
        "min_reviews": 12,
        "min_rating": 4.2,
        "result_limit": 15,
    }


def valid_observation() -> dict[str, object]:
    return {
        "query": " summer dress ",
        "rank": 1,
        "is_ad": None,
        "title": " Blue dress ",
        "url": " https://us.shein.com/blue-dress-p-123.html ",
        "product_id": " 123 ",
        "thumbnail_url": None,
    }


def valid_candidate() -> dict[str, object]:
    return {
        "identity": " shein-us:123 ",
        "platform": Platform.SHEIN_US,
        "title": " Blue dress ",
        "canonical_url": " https://us.shein.com/blue-dress-p-123.html ",
        "query_hits": [" blue dress ", "party dress"],
        "earliest_organic_rank": 2,
        "earliest_ad_rank": None,
        "sold_display": "1.2k sold",
        "sold_value": 1200,
        "reviews_display": "84",
        "reviews_value": 84,
        "rating_display": "4.7 de 5",
        "rating_value": 4.7,
        "match_level": "高度相似",
        "visual_features": [" square neck ", "floral"],
    }


class TaskModelTests(unittest.TestCase):
    def test_direct_constructor_normalizes_and_enforces_invariants(self) -> None:
        raw = valid_task()
        task = Task(**raw)  # type: ignore[arg-type]
        self.assertEqual(task.record_id, "rec-1")
        self.assertIs(task.platform, Platform.MERCADO_MX)
        for field, value in (("platform", "not-a-platform"), ("min_sold", True), ("min_rating", 6), ("result_limit", 0)):
            with self.subTest(field=field):
                invalid = valid_task()
                invalid[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    Task(**invalid)  # type: ignore[arg-type]

    def test_from_dict_normalizes_fields_and_is_frozen(self) -> None:
        task = Task.from_dict(valid_task())
        self.assertEqual(task.record_id, "rec-1")
        self.assertEqual(task.sku, "SKU-9")
        self.assertEqual(task.image_name, "dress.jpg")
        self.assertIs(task.platform, Platform.MERCADO_MX)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            task.sku = "changed"  # type: ignore[misc]

    def test_rejects_non_mapping_unknown_keys_and_invalid_thresholds(self) -> None:
        with self.assertRaises(TypeError):
            Task.from_dict([])  # type: ignore[arg-type]
        raw = valid_task()
        raw["extra"] = "surprise"
        with self.assertRaises(ValueError):
            Task.from_dict(raw)
        for field, value in (
            ("record_id", "  "),
            ("image_token", None),
            ("min_sold", -1),
            ("min_reviews", True),
            ("min_rating", 5.1),
            ("result_limit", 0),
        ):
            with self.subTest(field=field, value=value):
                raw = valid_task()
                raw[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    Task.from_dict(raw)


class ObservationModelTests(unittest.TestCase):
    def test_direct_constructor_normalizes_and_enforces_invariants(self) -> None:
        observation = Observation(**valid_observation())  # type: ignore[arg-type]
        self.assertEqual(observation.query, "summer dress")
        for field, value in (("rank", 0), ("rank", True), ("is_ad", 1), ("url", " ")):
            with self.subTest(field=field):
                invalid = valid_observation()
                invalid[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    Observation(**invalid)  # type: ignore[arg-type]

    def test_from_dict_normalizes_required_and_optional_strings(self) -> None:
        observation = Observation.from_dict(valid_observation())
        self.assertEqual(observation.query, "summer dress")
        self.assertEqual(observation.product_id, "123")
        self.assertIsNone(observation.thumbnail_url)

    def test_rejects_invalid_rank_ad_marker_optional_string_and_schema(self) -> None:
        for field, value in (
            ("rank", 0),
            ("rank", True),
            ("is_ad", 1),
            ("product_id", " "),
            ("title", ""),
        ):
            with self.subTest(field=field, value=value):
                raw = valid_observation()
                raw[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    Observation.from_dict(raw)
        raw = valid_observation()
        raw["unexpected"] = False
        with self.assertRaises(ValueError):
            Observation.from_dict(raw)


class VerifiedCandidateModelTests(unittest.TestCase):
    def test_direct_constructor_deep_freezes_sequences_and_enforces_invariants(self) -> None:
        raw = valid_candidate()
        raw["query_hits"] = {" blue dress ", "party dress"}
        raw["visual_features"] = [" square neck ", "floral"]
        candidate = VerifiedCandidate(**raw)  # type: ignore[arg-type]
        self.assertEqual(candidate.query_hits, frozenset({"blue dress", "party dress"}))
        self.assertEqual(candidate.visual_features, ("square neck", "floral"))
        for field, value in (
            ("platform", "bogus"),
            ("sold_value", True),
            ("rating_value", 6),
            ("earliest_organic_rank", 0),
            ("match_level", "exact"),
        ):
            with self.subTest(field=field):
                invalid = valid_candidate()
                invalid[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    VerifiedCandidate(**invalid)  # type: ignore[arg-type]

        no_rank = valid_candidate()
        no_rank.update({"earliest_organic_rank": None, "earliest_ad_rank": None})
        with self.assertRaises(ValueError):
            VerifiedCandidate(**no_rank)  # type: ignore[arg-type]

    def test_from_dict_normalizes_sequence_fields_to_immutable_types(self) -> None:
        candidate = VerifiedCandidate.from_dict(valid_candidate())
        self.assertEqual(candidate.query_hits, frozenset({"blue dress", "party dress"}))
        self.assertEqual(candidate.visual_features, ("square neck", "floral"))
        self.assertIs(candidate.platform, Platform.SHEIN_US)

    def test_rejects_invalid_metrics_ranks_and_match_level(self) -> None:
        for field, value in (
            ("sold_value", -1),
            ("reviews_value", True),
            ("rating_value", 5.01),
            ("earliest_organic_rank", 0),
            ("match_level", "exact"),
        ):
            with self.subTest(field=field, value=value):
                raw = valid_candidate()
                raw[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    VerifiedCandidate.from_dict(raw)

    def test_requires_a_rank_nonempty_queries_and_distinct_visual_features(self) -> None:
        for changes in (
            {"earliest_organic_rank": None, "earliest_ad_rank": None},
            {"query_hits": []},
            {"query_hits": [" "]},
            {"visual_features": []},
            {"visual_features": ["floral", " floral "]},
        ):
            with self.subTest(changes=changes):
                raw = valid_candidate()
                raw.update(changes)
                with self.assertRaises((TypeError, ValueError)):
                    VerifiedCandidate.from_dict(raw)

    def test_rejects_non_sequence_fields_and_unknown_keys(self) -> None:
        for field, value in (("query_hits", "blue dress"), ("visual_features", "floral")):
            raw = valid_candidate()
            raw[field] = value
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    VerifiedCandidate.from_dict(raw)
        raw = valid_candidate()
        raw["extra"] = 1
        with self.assertRaises(ValueError):
            VerifiedCandidate.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
