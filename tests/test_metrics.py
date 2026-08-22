import math
import unittest

from scripts.metrics import parse_count, parse_rating


class ParseCountTests(unittest.TestCase):
    def test_parses_hand_derived_display_counts(self) -> None:
        cases = {
            "500": 500,
            "+500 vendidos": 500,
            "1k+ sold": 1000,
            "1.2k sold": 1200,
            "1 mil vendidos": 1000,
            "1,234 vendidos": 1234,
            "未显示": None,
            "Best seller": None,
        }
        for displayed, expected in cases.items():
            with self.subTest(displayed=displayed):
                self.assertEqual(parse_count(displayed), expected)

    def test_accepts_only_non_negative_integral_numeric_values(self) -> None:
        self.assertEqual(parse_count(0), 0)
        self.assertEqual(parse_count(12.0), 12)
        for displayed in (True, False, -1, -0.5, 1.2, math.inf, math.nan):
            with self.subTest(displayed=displayed):
                self.assertIsNone(parse_count(displayed))

    def test_rejects_unsupported_or_non_integral_text(self) -> None:
        for displayed in ("-1 sold", "1.2345k sold", "2m sold", "500-ish", "sold 500", ""):
            with self.subTest(displayed=displayed):
                self.assertIsNone(parse_count(displayed))


class ParseRatingTests(unittest.TestCase):
    def test_parses_hand_derived_rating_displays(self) -> None:
        cases = {"4": 4.0, "4.7 de 5": 4.7, "未显示": None, "": None}
        for displayed, expected in cases.items():
            with self.subTest(displayed=displayed):
                self.assertEqual(parse_rating(displayed), expected)

    def test_accepts_only_finite_values_in_closed_rating_range(self) -> None:
        self.assertEqual(parse_rating(0), 0.0)
        self.assertEqual(parse_rating(5.0), 5.0)
        for displayed in (True, False, -0.1, 5.1, math.inf, math.nan):
            with self.subTest(displayed=displayed):
                self.assertIsNone(parse_rating(displayed))

    def test_rejects_text_without_unambiguous_rating_evidence(self) -> None:
        for displayed in ("Best seller", "4 stars out of 10", "rating 4.7", "4.7.2"):
            with self.subTest(displayed=displayed):
                self.assertIsNone(parse_rating(displayed))


if __name__ == "__main__":
    unittest.main()
