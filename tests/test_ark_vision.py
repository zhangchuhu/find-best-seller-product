import http.server
import io
import json
import socket
import socketserver
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from scripts.ark_vision import (
    ARK_CHAT_ENDPOINT,
    ArkVisionClient,
    ArkVisionError,
    VisualProfile,
)
from scripts.platforms import Platform


FIXTURE_KEY = "fixture-api-key-never-log"


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


def envelope(profile):
    return json.dumps({
        "choices": [{"message": {"content": json.dumps(profile)}}],
    }).encode("utf-8")


def content_envelope(content):
    return json.dumps({
        "choices": [{"message": {"content": content}}],
    }).encode("utf-8")


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


class RecordingOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        outcome = self.responses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)


class ArkTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.image_path = Path(self.directory.name) / "garment.jpg"
        self.image_path.write_bytes(b"\xff\xd8fixture-image\xff\xd9")
        self.environ = {
            "ARK_API_KEY": FIXTURE_KEY,
            "ARK_VISION_MODEL": "fixture-vision-model",
        }

    def test_missing_environment_fails_at_initialization_before_opener_or_file_io(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("opener must not be called")

        missing_image = Path(self.directory.name) / "does-not-exist.jpg"
        for environ in (
            {},
            {"ARK_API_KEY": " ", "ARK_VISION_MODEL": "model"},
            {"ARK_API_KEY": "key", "ARK_VISION_MODEL": "\t"},
        ):
            with self.subTest(environ=environ):
                with self.assertRaises(ArkVisionError):
                    client = ArkVisionClient(environ=environ, opener=opener)
                    client.analyze(missing_image, Platform.MERCADO_MX)
        self.assertEqual([], calls)

    def test_timeout_rejects_boolean_nonfinite_and_nonpositive_values(self):
        for timeout in (True, False, 0, -1, float("inf"), float("-inf"), float("nan")):
            with self.subTest(timeout=timeout):
                with self.assertRaises((TypeError, ValueError)):
                    ArkVisionClient(
                        environ=self.environ,
                        opener=lambda *args, **kwargs: None,
                        timeout=timeout,
                    )

    def test_default_timeout_is_300_seconds(self):
        opener = RecordingOpener([envelope(valid_profile())])
        client = ArkVisionClient(environ=self.environ, opener=opener)

        client.analyze(self.image_path, Platform.MERCADO_MX)

        self.assertEqual(300.0, opener.calls[0][1])

    def test_environment_timeout_overrides_default(self):
        opener = RecordingOpener([envelope(valid_profile())])
        environ = {**self.environ, "ARK_VISION_TIMEOUT_SECONDS": "42.5"}
        client = ArkVisionClient(environ=environ, opener=opener)

        client.analyze(self.image_path, Platform.MERCADO_MX)

        self.assertEqual(42.5, opener.calls[0][1])

    def test_explicit_timeout_takes_priority_over_environment(self):
        opener = RecordingOpener([envelope(valid_profile())])
        environ = {**self.environ, "ARK_VISION_TIMEOUT_SECONDS": "not-a-number"}
        client = ArkVisionClient(environ=environ, opener=opener, timeout=7.5)

        client.analyze(self.image_path, Platform.MERCADO_MX)

        self.assertEqual(7.5, opener.calls[0][1])

    def test_invalid_environment_timeout_fails_before_network_or_file_io(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("opener must not be called")

        for value in ("", " ", "zero", "0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                environ = {**self.environ, "ARK_VISION_TIMEOUT_SECONDS": value}
                with self.assertRaises(ArkVisionError) as context:
                    ArkVisionClient(environ=environ, opener=opener)
                if value.strip():
                    self.assertNotIn(value, str(context.exception))
        self.assertEqual([], calls)

    def test_posts_exact_endpoint_headers_model_timeout_and_jpeg_data_url(self):
        opener = RecordingOpener([envelope(valid_profile())])
        client = ArkVisionClient(environ=self.environ, opener=opener, timeout=7.5)

        profile = client.analyze(self.image_path, Platform.MERCADO_MX)

        self.assertEqual("vestido", profile.category)
        self.assertEqual(1, len(opener.calls))
        request, timeout = opener.calls[0]
        self.assertEqual(ARK_CHAT_ENDPOINT, request.full_url)
        self.assertEqual("POST", request.get_method())
        self.assertEqual(7.5, timeout)
        self.assertEqual("application/json", request.get_header("Content-type"))
        self.assertEqual(f"Bearer {FIXTURE_KEY}", request.get_header("Authorization"))
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual("fixture-vision-model", payload["model"])
        self.assertEqual(0, payload["temperature"])
        self.assertEqual({"type": "json_object"}, payload["response_format"])
        message = payload["messages"][0]
        self.assertEqual("user", message["role"])
        text_parts = [part["text"] for part in message["content"] if part["type"] == "text"]
        image_parts = [part["image_url"]["url"] for part in message["content"] if part["type"] == "image_url"]
        self.assertEqual(1, len(text_parts))
        self.assertEqual(1, len(image_parts))
        self.assertTrue(image_parts[0].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("fixture-image", image_parts[0])

    def test_prompt_names_exact_fields_cardinalities_and_platform_language(self):
        opener = RecordingOpener([envelope(valid_profile(Platform.SHEIN_US))])
        client = ArkVisionClient(environ=self.environ, opener=opener)

        client.analyze(self.image_path, Platform.SHEIN_US)

        payload = json.loads(opener.calls[0][0].data.decode("utf-8"))
        prompt = "\n".join(
            part["text"]
            for part in payload["messages"][0]["content"]
            if part["type"] == "text"
        )
        for field in valid_profile():
            self.assertIn(field, prompt)
        for constraint in ("1-8", "1-3", "2-6", "2-5", "exactly 3", "3-120", "en-US"):
            self.assertIn(constraint, prompt)
        self.assertIn("RESPONSE REFERENCE", prompt)
        self.assertIn('"style": [', prompt)
        self.assertIn('"selling_points": [', prompt)
        self.assertIn('"query_language": "en-US"', prompt)
        self.assertIn('"query_seeds": [', prompt)
        self.assertIn("Return only the raw JSON object", prompt)
        self.assertIn("replace every example value", prompt)
        self.assertIn("used unchanged as the final marketplace search queries", prompt)
        self.assertIn("Seed 1 must use only category/subtype words", prompt)
        self.assertIn("Seed 2 must repeat the category", prompt)
        self.assertIn("Seed 3 must repeat the category", prompt)
        self.assertNotIn("not final queries", prompt)
        self.assertIn("Never include color or size", prompt)

    def test_prompt_uses_a_mexican_spanish_response_reference_for_mercado(self):
        opener = RecordingOpener([envelope(valid_profile(Platform.MERCADO_MX))])
        client = ArkVisionClient(environ=self.environ, opener=opener)

        client.analyze(self.image_path, Platform.MERCADO_MX)

        payload = json.loads(opener.calls[0][0].data.decode("utf-8"))
        prompt = payload["messages"][0]["content"][0]["text"]
        reference = json.loads(prompt.split("because it appears below:\n", 1)[1])
        self.assertEqual("vestido", reference["category"])
        self.assertEqual(["romántico", "elegante"], reference["style"])
        self.assertEqual(3, len(reference["selling_points"]))
        self.assertEqual("es-MX", reference["query_language"])
        self.assertEqual("vestido corto", reference["query_seeds"][0])

    def test_seed_roles_use_explicit_selling_points_and_style(self):
        raw = valid_profile(Platform.SHEIN_US)
        raw["query_seeds"] = [
            "mini dress",
            "ruched waist mini dress",
            "romantic mini dress",
        ]

        profile = VisualProfile.from_dict(raw, Platform.SHEIN_US)

        self.assertEqual(tuple(raw["style"]), profile.style)
        self.assertEqual(tuple(raw["selling_points"]), profile.selling_points)
        self.assertEqual(tuple(raw["query_seeds"]), profile.query_seeds)

    def test_png_and_webp_use_mime_correct_data_urls(self):
        for suffix, expected_mime in ((".png", "image/png"), (".webp", "image/webp")):
            with self.subTest(suffix=suffix):
                image = Path(self.directory.name) / f"image{suffix}"
                image.write_bytes(b"image-bytes")
                opener = RecordingOpener([envelope(valid_profile())])
                ArkVisionClient(environ=self.environ, opener=opener).analyze(
                    image, Platform.MERCADO_MX,
                )
                payload = json.loads(opener.calls[0][0].data.decode("utf-8"))
                urls = [
                    part["image_url"]["url"]
                    for part in payload["messages"][0]["content"]
                    if part["type"] == "image_url"
                ]
                self.assertTrue(urls[0].startswith(f"data:{expected_mime};base64,"))

    def test_unsupported_extension_is_rejected_before_http(self):
        unsupported = Path(self.directory.name) / "garment.gif"
        unsupported.write_bytes(b"GIF89a")
        opener = RecordingOpener([envelope(valid_profile())])

        with self.assertRaises(ArkVisionError):
            ArkVisionClient(environ=self.environ, opener=opener).analyze(
                unsupported, Platform.MERCADO_MX,
            )

        self.assertEqual([], opener.calls)

    def test_image_size_limit_is_checked_before_read_or_http(self):
        opener = RecordingOpener([envelope(valid_profile())])
        client = ArkVisionClient(
            environ=self.environ, opener=opener, max_image_bytes=4,
        )

        with self.assertRaisesRegex(ArkVisionError, "size"):
            client.analyze(self.image_path, Platform.MERCADO_MX)

        self.assertEqual([], opener.calls)

    def test_transport_failures_are_bounded_and_redact_all_secret_shapes(self):
        hostile = (
            f"configured={FIXTURE_KEY} key=embedded-key token=embedded-token "
            "secret=embedded-secret Authorization: Bearer embedded-bearer "
            "Authorization embedded-auth "
            "data:image/png;base64,QUJDREVGRw=="
        )
        opener = RecordingOpener([urllib.error.URLError(hostile)])

        with self.assertRaises(ArkVisionError) as raised:
            ArkVisionClient(environ=self.environ, opener=opener).analyze(
                self.image_path, Platform.MERCADO_MX,
            )

        message = str(raised.exception)
        self.assertLessEqual(len(message), 500)
        self.assertNotIn(FIXTURE_KEY, message)
        for secret in (
            "embedded-key", "embedded-token", "embedded-secret",
            "embedded-bearer", "embedded-auth",
        ):
            self.assertNotIn(secret, message)
        self.assertNotIn("QUJDREVGRw==", message)

    def test_default_transport_rejects_redirect_before_target_contact(self):
        target_contacts = []
        source_contacts = []

        class TargetHandler(http.server.BaseHTTPRequestHandler):
            def _record(self):
                target_contacts.append({
                    "authorization_present": bool(self.headers.get("Authorization")),
                })
                body = envelope(valid_profile())
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _record
            do_POST = _record

            def log_message(self, format, *args):
                pass

        target_server, target_thread = self._start_server(TargetHandler)
        target_url = f"http://127.0.0.1:{target_server.server_port}/capture"

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                source_contacts.append({
                    "authorization_present": bool(self.headers.get("Authorization")),
                })
                self.send_response(302)
                self.send_header("Location", target_url)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()

            def log_message(self, format, *args):
                pass

        redirect_server, redirect_thread = self._start_server(RedirectHandler)
        redirect_url = f"http://127.0.0.1:{redirect_server.server_port}/analyze"
        self.addCleanup(self._stop_server, redirect_server, redirect_thread)
        self.addCleanup(self._stop_server, target_server, target_thread)

        with mock.patch("scripts.ark_vision.ARK_CHAT_ENDPOINT", redirect_url):
            with self.assertRaises(ArkVisionError):
                ArkVisionClient(environ=self.environ).analyze(
                    self.image_path, Platform.MERCADO_MX,
                )

        self.assertEqual(1, len(source_contacts))
        self.assertTrue(source_contacts[0]["authorization_present"])
        if target_contacts:
            self.fail("redirect target was contacted")

    def test_transport_errors_use_fixed_messages_without_exception_or_body_text(self):
        hostile = (
            f'{{"key":"quoted-key","token":"quoted-token",'
            f'"secret":"quoted-secret","configured":"{FIXTURE_KEY}"}} '
            "Authorization: Basic basic-credential extra-token "
            "Authorization: Custom custom-part-one custom-part-two "
            "request-body-sensitive response-body-sensitive "
            "data:image/webp;base64,U0VOU0lUSVZF"
        )
        cases = (
            (urllib.error.URLError(hostile), "Ark vision URL error"),
            (TimeoutError(hostile), "Ark vision request timed out"),
            (
                urllib.error.URLError(socket.timeout(hostile)),
                "Ark vision request timed out",
            ),
            (
                urllib.error.HTTPError(
                    "https://unapproved.example/secret",
                    418,
                    hostile,
                    {},
                    io.BytesIO(hostile.encode("utf-8")),
                ),
                "Ark vision HTTP error (status 418)",
            ),
            (
                urllib.error.HTTPError(
                    "https://unapproved.example/secret",
                    999999,
                    hostile,
                    {},
                    io.BytesIO(hostile.encode("utf-8")),
                ),
                "Ark vision HTTP error",
            ),
            (RuntimeError(hostile), "Ark vision transport error"),
        )
        for exception, expected in cases:
            with self.subTest(expected=expected):
                opener = RecordingOpener([exception])
                with self.assertRaises(ArkVisionError) as raised:
                    ArkVisionClient(environ=self.environ, opener=opener).analyze(
                        self.image_path, Platform.MERCADO_MX,
                    )
                if str(raised.exception) != expected:
                    self.fail("transport error did not use its fixed safe category")
                self.assertLessEqual(len(str(raised.exception)), 500)

    @staticmethod
    def _start_server(handler):
        class LoopbackHTTPServer(http.server.ThreadingHTTPServer):
            def server_bind(self):
                socketserver.TCPServer.server_bind(self)
                self.server_name, self.server_port = self.server_address

        server = LoopbackHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    @staticmethod
    def _stop_server(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class ArkResponseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.image_path = Path(self.directory.name) / "garment.jpeg"
        self.image_path.write_bytes(b"fixture-image")
        self.environ = {
            "ARK_API_KEY": FIXTURE_KEY,
            "ARK_VISION_MODEL": "fixture-vision-model",
        }

    def client(self, responses):
        opener = RecordingOpener(responses)
        return ArkVisionClient(environ=self.environ, opener=opener), opener

    def assert_profile_rejected(self, profile, platform=Platform.MERCADO_MX):
        response = envelope(profile)
        client, opener = self.client([response, response, response])
        with self.assertRaises(ArkVisionError) as raised:
            client.analyze(self.image_path, platform)
        self.assertEqual(3, len(opener.calls))
        self.assertIn("3 attempts", str(raised.exception))
        self.assertIn("profile", str(raised.exception))
        return str(raised.exception)

    def test_accepts_plain_and_single_json_fenced_profile(self):
        raw = valid_profile()
        contents = (
            json.dumps(raw),
            f"  \n```json\n{json.dumps(raw)}\n```\n\t",
        )
        for content in contents:
            with self.subTest(content=content[:12]):
                client, opener = self.client([content_envelope(content)])
                profile = client.analyze(self.image_path, Platform.MERCADO_MX)
                self.assertEqual(tuple(raw["query_seeds"]), profile.query_seeds)
                self.assertFalse(hasattr(profile, "queries"))
                self.assertEqual(1, len(opener.calls))

    def test_rejects_malformed_extra_prose_multiple_fences_and_non_object_roots(self):
        raw = json.dumps(valid_profile())
        invalid_contents = (
            "{not json}",
            f"Here is the result: {raw}",
            f"```json\n{raw}\n``` trailing prose",
            f"```json\n{raw}\n```\n```json\n{raw}\n```",
            "[]",
        )
        for content in invalid_contents:
            with self.subTest(content=content[:24]):
                response = content_envelope(content)
                client, opener = self.client([response, response, response])
                with self.assertRaises(ArkVisionError) as raised:
                    client.analyze(self.image_path, Platform.MERCADO_MX)
                self.assertEqual(3, len(opener.calls))
                self.assertIn("3 attempts", str(raised.exception))
                self.assertIn("json", str(raised.exception))

    def test_rejects_missing_or_empty_success_envelope(self):
        invalid_envelopes = (
            b"{}",
            b'{"choices": []}',
            b'{"choices": [{}]}',
            b'{"choices": [{"message": {}}]}',
        )
        for response in invalid_envelopes:
            with self.subTest(response=response):
                client, opener = self.client([response, response, response])
                with self.assertRaises(ArkVisionError) as raised:
                    client.analyze(self.image_path, Platform.MERCADO_MX)
                self.assertEqual(3, len(opener.calls))
                self.assertIn("envelope", str(raised.exception))

    def test_rejects_non_mapping_missing_and_unknown_profile_fields(self):
        missing = valid_profile()
        del missing["fit"]
        unknown = valid_profile()
        unknown["confidence"] = "high"
        response = content_envelope("[]")
        client, opener = self.client([response, response, response])
        with self.assertRaises(ArkVisionError) as raised:
            client.analyze(self.image_path, Platform.MERCADO_MX)
        self.assertEqual(3, len(opener.calls))
        self.assertIn("json", str(raised.exception))

        for raw in (missing, unknown):
            with self.subTest(raw_type=type(raw).__name__, fields=len(raw)):
                self.assert_profile_rejected(raw)

    def test_rejects_invalid_collection_sizes_and_non_string_items(self):
        mutations = (
            ("style", []),
            ("style", [str(index) for index in range(4)]),
            ("selling_points", ["only one"]),
            ("selling_points", [str(index) for index in range(7)]),
            ("construction", []),
            ("construction", [str(index) for index in range(9)]),
            ("defining_features", ["only one"]),
            ("defining_features", [str(index) for index in range(6)]),
            ("exclusions", []),
            ("exclusions", [str(index) for index in range(9)]),
            ("query_seeds", ["one seed", "two seed"]),
            ("construction", [True]),
            ("style", [True]),
            ("selling_points", ["valid one", 2]),
            ("defining_features", ["valid one", 2]),
            ("exclusions", "not a list"),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                raw = valid_profile()
                raw[field] = value
                self.assert_profile_rejected(raw)

    def test_rejects_trimmed_duplicates_blank_scalars_and_invalid_seed_lengths(self):
        mutations = (
            ("category", "   "),
            ("construction", [" seam ", "seam"]),
            ("query_seeds", ["same seed", " same seed ", "third seed"]),
            ("query_seeds", ["vestido", "ab", "vestido fiesta"]),
            ("query_seeds", ["vestido", "x" * 121, "vestido fiesta"]),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                raw = valid_profile()
                raw[field] = value
                self.assert_profile_rejected(raw)

    def test_wrong_platform_language_label_is_a_retryable_profile_failure(self):
        mercado = valid_profile()
        mercado["query_language"] = "en-US"
        self.assert_profile_rejected(mercado, Platform.MERCADO_MX)

        shein = valid_profile(Platform.SHEIN_US)
        shein["query_language"] = "es-MX"
        self.assert_profile_rejected(shein, Platform.SHEIN_US)

    def test_query_seeds_enforce_indexed_roles_category_and_market_language(self):
        mutations = {
            "generic": ["women fashion", "vestido bodycon", "vestido fiesta"],
            "wrong language": ["vestido corto", "vestido bodycon", "party dress"],
            "wrong category": ["zapatos", "vestido bodycon", "vestido fiesta"],
            "seed one includes disallowed style": ["vestido fiesta", "vestido bodycon", "vestido fiesta noche"],
            "seed two missing silhouette or construction": ["vestido corto", "vestido fiesta", "vestido fiesta noche"],
            "seed three includes construction detail": ["vestido corto", "vestido bodycon", "vestido dobladillo"],
            "color": ["vestido negro", "vestido bodycon", "vestido fiesta"],
            "size": ["vestido corto", "vestido talla M", "vestido fiesta"],
        }
        for label, query_seeds in mutations.items():
            with self.subTest(case=label):
                raw = valid_profile()
                raw["query_seeds"] = query_seeds
                self.assert_profile_rejected(raw)

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

    def test_succeeds_on_exact_third_invalid_response_retry(self):
        invalid = content_envelope("not JSON")
        valid = envelope(valid_profile())
        client, opener = self.client([invalid, invalid, valid])

        profile = client.analyze(self.image_path, Platform.MERCADO_MX)

        self.assertEqual("bodycon", profile.silhouette)
        self.assertEqual(3, len(opener.calls))

    def test_three_invalid_responses_raise_bounded_category_without_raw_content(self):
        raw_secret = f"invalid profile Authorization: Bearer {FIXTURE_KEY}"
        response = content_envelope(raw_secret)
        client, opener = self.client([response, response, response])

        with self.assertRaises(ArkVisionError) as raised:
            client.analyze(self.image_path, Platform.MERCADO_MX)

        message = str(raised.exception)
        self.assertEqual(3, len(opener.calls))
        self.assertLessEqual(len(message), 500)
        self.assertIn("3 attempts", message)
        self.assertIn("json", message)
        self.assertNotIn(raw_secret, message)
        self.assertNotIn(FIXTURE_KEY, message)

    def test_transport_failure_is_not_retried(self):
        opener = RecordingOpener([
            TimeoutError("timed out"),
            envelope(valid_profile()),
        ])
        client = ArkVisionClient(environ=self.environ, opener=opener)

        with self.assertRaises(ArkVisionError):
            client.analyze(self.image_path, Platform.MERCADO_MX)

        self.assertEqual(1, len(opener.calls))

    def test_non_text_response_body_is_retried_as_response_validation(self):
        client, opener = self.client([123, 123, 123])

        with self.assertRaises(ArkVisionError) as raised:
            client.analyze(self.image_path, Platform.MERCADO_MX)

        self.assertEqual(3, len(opener.calls))
        self.assertIn("response", str(raised.exception))

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


if __name__ == "__main__":
    unittest.main()
