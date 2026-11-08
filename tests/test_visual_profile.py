import copy
import unittest

from scripts.platforms import Platform
from scripts.visual_profile import ProductVisualProfile, STYLES, SCENES, SELLING_POINTS


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            'category_code': 'womens_pants_set', 'category': 'conjunto',
            'subtype': 'conjunto de blusa y pantalón',
            'silhouette': 'pantalón de pierna ancha y blusa entallada',
            'fit': 'entallado arriba y holgado abajo', 'color': 'negro',
            'style_codes': ['elegant'], 'style': ['elegante'],
            'selling_point_codes': ['front_knot', 'high_waist'],
            'selling_points': ['nudo frontal', 'tiro alto'],
            'construction': ['mangas largas'],
            'defining_features': ['nudo frontal', 'dos piezas'],
            'exclusions': ['top sin mangas'],
            'scene_codes': ['office'], 'use_scene': ['oficina'],
            'query_language': 'es-MX',
            'query_seeds': ['conjunto de blusa y pantalón',
                            'conjunto con nudo frontal',
                            'conjunto elegante para oficina'],
        }
    def test_example_profile_round_trips(self):
        self.assertEqual(self.data, ProductVisualProfile.from_dict(self.data, Platform.MERCADO_MX).to_dict())

    def test_invalid_pairs_are_rejected(self):
        for field, value in [('style_codes', ['unknown']), ('style', ['elegant']),
                             ('scene_codes', ['unknown']), ('use_scene', ['office']),
                             ('selling_points', ['tiro alto', 'nudo frontal']),
                             ('selling_point_codes', ['front_knot', 'front_knot']),
                             ('category_code', 'not_a_category')]:
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                data[field] = value
                with self.assertRaises(ValueError):
                    ProductVisualProfile.from_dict(data, Platform.MERCADO_MX)

    def test_cross_market_and_low_value_terms_are_rejected(self):
        for query in ('conjunto red nudo frontal', 'conjunto vest nudo frontal',
                      'conjunto talla M nudo frontal'):
            with self.subTest(query=query):
                data = copy.deepcopy(self.data)
                data['query_seeds'][1] = query
                with self.assertRaises(ValueError):
                    ProductVisualProfile.from_dict(data, Platform.MERCADO_MX)

    def test_accented_and_unaccented_category_are_equivalent(self):
        data = copy.deepcopy(self.data)
        data['query_seeds'][0] = 'conjunto de blusa y pantalon'
        self.assertEqual('conjunto de blusa y pantalon',
                         ProductVisualProfile.from_dict(data, Platform.MERCADO_MX).query_seeds[0])

    def test_enum_tables_have_only_real_codes(self):
        self.assertEqual(30, len(STYLES))
        self.assertEqual(39, len(SCENES))
        self.assertNotIn('selling_point_code', SELLING_POINTS)
        self.assertEqual(('nudo frontal', 'front knot'), SELLING_POINTS['front_knot'])

    def test_english_profile_accepts_canonical_values(self):
        data = copy.deepcopy(self.data)
        data.update(category='set', subtype='blouse pants set',
                    silhouette='wide leg pants', style=['elegant'],
                    selling_points=['front knot', 'high waist'],
                    construction=['long sleeves'], defining_features=['front knot', 'two pieces'],
                    exclusions=['sleeveless top'], use_scene=['office'], query_language='en-US',
                    query_seeds=['blouse pants set', 'set front knot', 'elegant set office'])
        result = ProductVisualProfile.from_dict(data, Platform.SHEIN_US)
        self.assertEqual('front knot', result.selling_points[0])

    def test_market_language_must_match_platform(self):
        with self.assertRaises(ValueError):
            ProductVisualProfile.from_dict(self.data, Platform.SHEIN_US)

    def test_connectors_do_not_satisfy_seed_roles(self):
        for query in ('de fiesta', 'y oficina', 'con oficina'):
            data = copy.deepcopy(self.data)
            data['query_seeds'][2] = query
            with self.subTest(query=query), self.assertRaises(ValueError):
                ProductVisualProfile.from_dict(data, Platform.MERCADO_MX)

    def test_connectors_cannot_supply_selling_or_scene_evidence(self):
        data = copy.deepcopy(self.data)
        data['construction'] = ['botones de madera']
        data['silhouette'] = 'corte de pierna'
        data['query_seeds'][1] = 'conjunto de'
        with self.assertRaises(ValueError):
            ProductVisualProfile.from_dict(data, Platform.MERCADO_MX)

    def test_category_word_alone_is_not_selling_point(self):
        data = copy.deepcopy(self.data)
        data['query_seeds'][1] = 'conjunto pantalón'
        with self.assertRaises(ValueError):
            ProductVisualProfile.from_dict(data, Platform.MERCADO_MX)
        data = copy.deepcopy(self.data)
        data['scene_codes'] = ['weekend']
        data['use_scene'] = ['fin de semana']
        data['query_seeds'][2] = 'conjunto de'
        with self.assertRaises(ValueError):
            ProductVisualProfile.from_dict(data, Platform.MERCADO_MX)

    def test_category_code_must_match_text(self):
        data = copy.deepcopy(self.data)
        data['category_code'] = 'dress'
        with self.assertRaises(ValueError):
            ProductVisualProfile.from_dict(data, Platform.MERCADO_MX)

    def test_each_mapping_accepts_its_market_value_and_rejects_mismatch(self):
        for codes_field, values_field, mapping in (
            ('style_codes', 'style', STYLES), ('scene_codes', 'use_scene', SCENES),
            ('selling_point_codes', 'selling_points', SELLING_POINTS),
        ):
            for code, (spanish, english) in mapping.items():
                for platform, expected in ((Platform.MERCADO_MX, spanish), (Platform.SHEIN_US, english)):
                    with self.subTest(field=codes_field, code=code, platform=platform):
                        data = copy.deepcopy(self.data)
                        if platform is Platform.SHEIN_US:
                            data.update(category='set', subtype='blouse pants set',
                                        silhouette='wide leg pants', style=['elegant'],
                                        selling_points=['front knot', 'high waist'],
                                        construction=['long sleeves'], defining_features=['front knot', 'two pieces'],
                                        exclusions=['sleeveless top'], use_scene=['office'], query_language='en-US',
                                        query_seeds=['blouse pants set', 'set front knot', 'set office'])
                        if codes_field == 'selling_point_codes':
                            other = 'high_waist' if code != 'high_waist' else 'front_knot'
                            data[codes_field] = [code, other]
                            data[values_field] = [expected, mapping[other][0 if platform is Platform.MERCADO_MX else 1]]
                            data['query_seeds'][1] = ('conjunto ' if platform is Platform.MERCADO_MX else 'set ') + data[values_field][1]
                        else:
                            data[codes_field] = [code]
                            data[values_field] = [expected]
                            if codes_field == 'scene_codes':
                                data['query_seeds'][2] = ('conjunto ' if platform is Platform.MERCADO_MX else 'set ') + expected
                        self.assertEqual(tuple(data[codes_field]), ProductVisualProfile.from_dict(data, platform).__getattribute__(codes_field))
                        invalid = copy.deepcopy(data)
                        invalid[values_field][0] = '__wrong__'
                        with self.assertRaises(ValueError):
                            ProductVisualProfile.from_dict(invalid, platform)
