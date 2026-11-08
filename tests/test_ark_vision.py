import unittest
from scripts.ark_vision import VisualProfile
from scripts.platforms import Platform


def valid_profile(platform=Platform.MERCADO_MX):
    if platform is Platform.MERCADO_MX:
        language = "es-MX"
        query_seeds = [
            "vestido corto",
            "vestido bodycon",
            "vestido fiesta",
        ]
        category = "vestido"
        subtype = "vestido corto"
        construction = ["manga larga", "escote cuadrado"]
        features = ["cintura fruncida", "dobladillo corto"]
        style = ["romántico", "elegante"]
        selling_points = ["cintura fruncida", "manga larga", "escote cuadrado"]
    else:
        language = "en-US"
        query_seeds = [
            "mini dress",
            "bodycon mini dress",
            "party dress",
        ]
        category = "dress"
        subtype = "mini dress"
        construction = ["long sleeves", "square neckline"]
        features = ["ruched waist", "short hem"]
        style = ["romantic", "elegant"]
        selling_points = ["ruched waist", "long sleeves", "square neckline"]
    return {
        "category": category,
        "subtype": subtype,
        "silhouette": "bodycon",
        "fit": "fitted",
        "color": "black",
        "style": style,
        "selling_points": selling_points,
        "construction": construction,
        "defining_features": features,
        "exclusions": ["maxi length"],
        "use_scene": "fiesta" if platform is Platform.MERCADO_MX else "party",
        "query_language": language,
        "query_seeds": query_seeds,
    }


class LegacyVisualProfileTests(unittest.TestCase):
    def test_visual_profile_rejects_low_value_seed_terms_from_either_market_language(self):
        cases = (
            (Platform.SHEIN_US, "bodycon rojo mini dress", None),
            (Platform.SHEIN_US, "bodycon unitalla mini dress", None),
            (Platform.SHEIN_US, "bodycon apricot mini dress", "apricot beige"),
            (Platform.MERCADO_MX, "vestido bodycon red", None),
            (Platform.MERCADO_MX, "vestido bodycon one size", None),
        )
        for platform, second_seed, source_color in cases:
            with self.subTest(platform=platform.value, second_seed=second_seed):
                raw = valid_profile(platform)
                raw["query_seeds"][1] = second_seed
                if source_color is not None:
                    raw["color"] = source_color
                with self.assertRaisesRegex(ValueError, "color or size"):
                    VisualProfile.from_dict(raw, platform)

    def test_seed_three_accepts_explicit_style_evidence(self):
        raw = valid_profile()
        raw["style"] = ["boho"]
        raw["query_seeds"] = ["vestido corto", "vestido bodycon", "vestido boho"]

        profile = VisualProfile.from_dict(raw, Platform.MERCADO_MX)

        self.assertEqual(("vestido corto", "vestido bodycon", "vestido boho"), profile.query_seeds)

    def test_seed_three_accepts_accented_spanish_style_evidence(self):
        raw = valid_profile()
        raw["defining_features"] = ["estilo romántico", "dobladillo corto"]
        raw["query_seeds"] = ["vestido corto", "vestido bodycon", "vestido romántico"]

        profile = VisualProfile.from_dict(raw, Platform.MERCADO_MX)

        self.assertEqual(
            ("vestido corto", "vestido bodycon", "vestido romántico"),
            profile.query_seeds,
        )

    def test_direct_construction_and_round_trip_normalize_validate_and_freeze(self):
        raw = valid_profile()
        raw["category"] = " dress "
        raw["construction"] = [" manga larga ", "escote cuadrado"]
        raw["query_seeds"] = [f" {seed} " for seed in raw["query_seeds"]]
        profile = VisualProfile(**raw)

        self.assertEqual("dress", profile.category)
        self.assertEqual(("manga larga", "escote cuadrado"), profile.construction)
        self.assertIsInstance(profile.construction, tuple)
        self.assertIsInstance(profile.query_seeds, tuple)
        with self.assertRaises(AttributeError):
            profile.category = "changed"

        restored = VisualProfile.from_dict(
            profile.to_dict(), Platform.MERCADO_MX,
        )
        self.assertEqual(profile, restored)
        self.assertEqual(list(profile.query_seeds), restored.to_dict()["query_seeds"])

        invalid = valid_profile()
        invalid["construction"] = [False]
        with self.assertRaises((TypeError, ValueError)):
            VisualProfile(**invalid)
