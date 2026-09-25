import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "mcp_servers/fruityvice-mcp/app.py"
if not MODULE_PATH.is_file():
    raise unittest.SkipTest(
        "FruityVice source is omitted because its pinned upstream has no license"
    )
SPEC = importlib.util.spec_from_file_location("fruityvice_app", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
fruityvice_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fruityvice_app)


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


class FruityViceClientTests(unittest.TestCase):
    def test_success_response_is_normalized(self):
        payload = (
            b'{"name":"Apple","family":"Rosaceae","genus":"Malus",'
            b'"order":"Rosales","nutritions":{"calories":52},"id":6}'
        )
        with patch.object(
            fruityvice_app,
            "urlopen",
            return_value=FakeResponse(payload),
        ):
            result = fruityvice_app.get_fruit_info("apple")

        self.assertEqual(result["name"], "Apple")
        self.assertEqual(result["nutritions"]["calories"], 52)
        self.assertEqual(result["source"], "FruityVice")

    def test_fruit_name_is_url_encoded(self):
        payload = b'{"name":"Dragon Fruit","nutritions":{},"id":1}'
        with patch.object(
            fruityvice_app,
            "urlopen",
            return_value=FakeResponse(payload),
        ) as mocked:
            fruityvice_app.get_fruit_info("dragon fruit")

        request = mocked.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/dragon%20fruit"))

    def test_not_found_is_non_retryable(self):
        error = HTTPError(
            "https://example.test",
            404,
            "Not Found",
            {},
            io.BytesIO(),
        )
        try:
            with patch.object(fruityvice_app, "urlopen", side_effect=error):
                result = fruityvice_app.get_fruit_info("not-a-fruit")
        finally:
            error.close()

        self.assertIn("not found", result["error"])
        self.assertFalse(result["retryable"])

    def test_network_error_is_retryable(self):
        with patch.object(
            fruityvice_app,
            "urlopen",
            side_effect=URLError("offline"),
        ):
            result = fruityvice_app.get_fruit_info("apple")

        self.assertIn("network error", result["error"])
        self.assertTrue(result["retryable"])

    def test_empty_name_is_rejected_without_network_call(self):
        with patch.object(fruityvice_app, "urlopen") as mocked:
            result = fruityvice_app.get_fruit_info("   ")

        self.assertIn("must not be empty", result["error"])
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
