import unittest

from scripts.query_terms import contains_color_or_size


class ContainsColorOrSizeTests(unittest.TestCase):
    def test_color_and_size_terms_are_detected_without_rejecting_construction(self) -> None:
        rejected = (
            ("red dress", "en-US"),
            ("vestido negro", "es-MX"),
            ("plus size dress", "en-US"),
            ("petite dress", "en-US"),
            ("tall women dress", "en-US"),
            ("size 12 dress", "en-US"),
            ("vestido talla M", "es-MX"),
            ("XL cocktail dress", "en-US"),
            ("champagne dress", "en-US"),
            ("vestido vino", "es-MX"),
            ("one size dress", "en-US"),
            ("vestido unitalla", "es-MX"),
            ("vestido talla única", "es-MX"),
            ("vestido talla unica", "es-MX"),
            ("vestido tamaño único", "es-MX"),
            ("vestido tamano unico", "es-MX"),
        )
        accepted = (
            ("3D flower dress", "en-US"),
            ("long sleeve dress", "en-US"),
            ("vestido manga larga", "es-MX"),
            ("A-line mini dress", "en-US"),
        )

        for text, language in rejected:
            with self.subTest(text=text, language=language):
                self.assertTrue(contains_color_or_size(text, language))
        for text, language in accepted:
            with self.subTest(text=text, language=language):
                self.assertFalse(contains_color_or_size(text, language))

    def test_source_color_is_forbidden_but_its_modifiers_alone_are_not(self) -> None:
        self.assertTrue(contains_color_or_size("dark blue dress", "en-US", "dark blue"))
        self.assertFalse(contains_color_or_size("dark dress", "en-US", "dark blue"))
        self.assertFalse(contains_color_or_size("bright dress", "en-US", "bright red"))
        self.assertFalse(contains_color_or_size("solid dress", "en-US", "solid black"))
        self.assertTrue(contains_color_or_size("aubergine dress", "en-US", "aubergine"))

    def test_each_supported_market_rejects_terms_maintained_for_either_language(self) -> None:
        cases = (
            ("rojo mini dress", "en-US"),
            ("unitalla mini dress", "en-US"),
            ("red vestido corto", "es-MX"),
            ("one size vestido corto", "es-MX"),
        )
        for text, language in cases:
            with self.subTest(text=text, language=language):
                self.assertTrue(contains_color_or_size(text, language))

    def test_multiword_source_color_rejects_each_meaningful_token(self) -> None:
        for source_color in ("apricot beige", "dark apricot and beige", "dark apricot / beige"):
            with self.subTest(source_color=source_color):
                self.assertTrue(
                    contains_color_or_size("apricot mini dress", "en-US", source_color)
                )
                self.assertTrue(
                    contains_color_or_size("beige mini dress", "en-US", source_color)
                )
        for source_color in ("dark", "solid", "dark and solid", "dark / solid"):
            with self.subTest(source_color=source_color):
                self.assertFalse(
                    contains_color_or_size("mini dress", "en-US", source_color)
                )

    def test_unsupported_language_still_fails_closed(self) -> None:
        self.assertTrue(contains_color_or_size("mini dress", "fr-FR"))

    def test_unicode_format_characters_fail_closed_instead_of_obscuring_terms(self) -> None:
        """Catch normalization that silently joins zero-width characters into harmless tokens."""
        for text, language in (
            ("re\u200bd dress", "en-US"),
            ("r\u200bedo vestido", "es-MX"),
            ("pl\u200bus size dress", "en-US"),
        ):
            with self.subTest(text=text, language=language):
                self.assertTrue(contains_color_or_size(text, language))


if __name__ == "__main__":
    unittest.main()
