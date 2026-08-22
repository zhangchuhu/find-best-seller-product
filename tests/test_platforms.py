import unittest

from scripts.platforms import (
    Platform,
    UnsupportedPlatformError,
    canonicalize_url,
    product_identity,
    route_platform,
)


class RoutePlatformTests(unittest.TestCase):
    def test_routes_supported_normalized_hosts(self) -> None:
        cases = {
            "www.mercadolibre.com.mx": Platform.MERCADO_MX,
            "https://ARTICULO.MERCADOLIBRE.COM.MX/MLM-123-name": Platform.MERCADO_MX,
            "https://us.shein.com/product-p-12345.html": Platform.SHEIN_US,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(route_platform(value), expected)

    def test_rejects_apex_lookalikes_and_unsupported_hosts(self) -> None:
        for value in (
            "mercadolibre.com.mx",
            "https://mercadolibre.com.mx.evil.example/MLM-123",
            "https://us.shein.com.evil.example/p-123.html",
            "https://shein.com/p-123.html",
            "not a host",
        ):
            with self.subTest(value=value):
                with self.assertRaises(UnsupportedPlatformError):
                    route_platform(value)


class CanonicalUrlTests(unittest.TestCase):
    def test_requires_https_and_matching_platform(self) -> None:
        with self.assertRaises(UnsupportedPlatformError):
            canonicalize_url(Platform.MERCADO_MX, "http://articulo.mercadolibre.com.mx/MLM-123")
        with self.assertRaises(UnsupportedPlatformError):
            canonicalize_url(Platform.SHEIN_US, "https://articulo.mercadolibre.com.mx/MLM-123")

    def test_removes_fragment_and_tracking_but_preserves_product_options(self) -> None:
        mercado = canonicalize_url(
            Platform.MERCADO_MX,
            "https://ARTICULO.MERCADOLIBRE.COM.MX/MLM-123456-name-_JM?utm_source=ad&variation=7#details",
        )
        shein = canonicalize_url(
            Platform.SHEIN_US,
            "https://us.shein.com/dress-p-99887.html?size=m&campaignId=summer&advertising_id=42&ad_source=feed&color=red&fbclid=facebook-click&gclid=google-click&msclkid=bing-click&ref_id=home&tracking_id=9&utm_medium=cpc#reviews",
        )
        self.assertEqual(
            mercado,
            "https://articulo.mercadolibre.com.mx/MLM-123456-name-_JM?variation=7",
        )
        self.assertEqual(shein, "https://us.shein.com/dress-p-99887.html?color=red&size=m")


class ProductIdentityTests(unittest.TestCase):
    def test_extracts_and_normalizes_stable_url_ids(self) -> None:
        mercado = product_identity(
            Platform.MERCADO_MX,
            "https://articulo.mercadolibre.com.mx/MLM-123456-name-_JM?utm_source=x",
        )
        shein = product_identity(
            Platform.SHEIN_US,
            "https://us.shein.com/dress-p-99887.html?ref=home",
        )
        self.assertEqual(mercado, "mercado-libre-mx:MLM123456")
        self.assertEqual(shein, "shein-us:99887")

    def test_sanitizes_valid_explicit_ids_and_rejects_wrong_grammar(self) -> None:
        mercado_url = "https://www.mercadolibre.com.mx/some-product"
        shein_url = "https://us.shein.com/some-product.html"
        self.assertEqual(
            product_identity(Platform.MERCADO_MX, mercado_url, " mlm-123_456 "),
            "mercado-libre-mx:MLM123456",
        )
        self.assertEqual(
            product_identity(Platform.SHEIN_US, shein_url, " goods_99887 "),
            "shein-us:99887",
        )
        for platform, url, explicit_id in (
            (Platform.MERCADO_MX, mercado_url, "99887"),
            (Platform.SHEIN_US, shein_url, "MLM123456"),
            (Platform.SHEIN_US, shein_url, "goods_bad"),
        ):
            with self.subTest(explicit_id=explicit_id):
                with self.assertRaises(ValueError):
                    product_identity(platform, url, explicit_id)

    def test_falls_back_to_the_canonical_url(self) -> None:
        url = "https://us.shein.com/some-product.html?gclid=click&advertising_campaign=x&utm_source=ad&color=blue#top"
        self.assertEqual(
            product_identity(Platform.SHEIN_US, url),
            "shein-us:https://us.shein.com/some-product.html?color=blue",
        )


if __name__ == "__main__":
    unittest.main()
