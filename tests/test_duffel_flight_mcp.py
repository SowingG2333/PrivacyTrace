import asyncio
import importlib.util
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "mcp_servers/duffel-flight-mcp/server.py"
SPEC = importlib.util.spec_from_file_location("duffel_flight_server", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
duffel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(duffel)


def future_date(days: int = 30) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


class DuffelInputTests(unittest.TestCase):
    def test_flight_request_is_validated_and_bounded(self):
        request_spec = duffel.build_flight_request(
            origin="sha",
            destination="pek",
            departure_date=future_date(),
            return_date=future_date(37),
            adults=1,
            children=1,
            infants=1,
            cabin_class="PREMIUM_ECONOMY",
            non_stop=True,
            max_connections=2,
            max_results=8,
        )

        data = request_spec["body"]["data"]
        self.assertEqual(data["slices"][0]["origin"], "SHA")
        self.assertEqual(data["slices"][0]["destination"], "PEK")
        self.assertEqual(data["slices"][1]["origin"], "PEK")
        self.assertEqual(data["cabin_class"], "premium_economy")
        self.assertEqual(data["max_connections"], 0)
        self.assertEqual(
            [passenger["type"] for passenger in data["passengers"]],
            ["adult", "child", "infant_without_seat"],
        )
        self.assertEqual(request_spec["max_results"], 8)

    def test_invalid_iata_code_is_rejected(self):
        with self.assertRaisesRegex(duffel.DuffelError, "three-letter"):
            duffel.build_flight_request(
                origin="Shanghai",
                destination="PEK",
                departure_date=future_date(),
                return_date=None,
                adults=1,
                children=0,
                infants=0,
                cabin_class="economy",
                non_stop=False,
                max_connections=1,
                max_results=8,
            )

    def test_total_passengers_are_bounded(self):
        with self.assertRaisesRegex(duffel.DuffelError, "total passengers"):
            duffel.build_flight_request(
                origin="SHA",
                destination="PEK",
                departure_date=future_date(),
                return_date=None,
                adults=8,
                children=2,
                infants=0,
                cabin_class="economy",
                non_stop=False,
                max_connections=1,
                max_results=8,
            )

    def test_place_query_limits_result_count(self):
        with self.assertRaisesRegex(duffel.DuffelError, "between 1 and 20"):
            duffel.build_place_query(keyword="Shanghai", max_results=100)

    def test_flight_response_is_compact_and_search_only(self):
        request_spec = {
            "max_results": 1,
            "input": {
                "origin": "SHA",
                "destination": "PEK",
                "departure_date": future_date(),
            },
        }
        payload = {
            "data": {
                "id": "orq_test",
                "offers": [
                    {
                        "id": "off_test",
                        "live_mode": False,
                        "expires_at": f"{future_date()}T00:30:00Z",
                        "total_currency": "GBP",
                        "total_amount": "99.00",
                        "tax_amount": "20.00",
                        "owner": {
                            "name": "Duffel Airways",
                            "iata_code": "ZZ",
                        },
                        "payment_requirements": {
                            "requires_instant_payment": False,
                        },
                        "slices": [
                            {
                                "duration": "PT2H20M",
                                "origin": {
                                    "type": "airport",
                                    "name": "Hongqiao",
                                    "iata_code": "SHA",
                                },
                                "destination": {
                                    "type": "airport",
                                    "name": "Capital",
                                    "iata_code": "PEK",
                                },
                                "segments": [
                                    {
                                        "origin": {
                                            "type": "airport",
                                            "name": "Hongqiao",
                                            "iata_code": "SHA",
                                        },
                                        "destination": {
                                            "type": "airport",
                                            "name": "Capital",
                                            "iata_code": "PEK",
                                        },
                                        "departing_at": (
                                            f"{future_date()}T08:00:00"
                                        ),
                                        "arriving_at": (
                                            f"{future_date()}T10:20:00"
                                        ),
                                        "duration": "PT2H20M",
                                        "marketing_carrier_flight_number": "ZZ101",
                                        "marketing_carrier": {
                                            "name": "Duffel Airways",
                                            "iata_code": "ZZ",
                                        },
                                        "operating_carrier": {
                                            "name": "Duffel Airways",
                                            "iata_code": "ZZ",
                                        },
                                        "aircraft": {
                                            "name": "Airbus A320",
                                            "iata_code": "320",
                                        },
                                        "stops": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        }

        result = duffel.normalize_flight_response(
            payload,
            request_spec,
            credential_mode="test",
        )

        self.assertTrue(result["search_only"])
        self.assertFalse(result["booking_capability_exposed"])
        self.assertEqual(result["credential_mode"], "test")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["offer_request_id"], "orq_test")
        segment = result["offers"][0]["slices"][0]["segments"][0]
        self.assertEqual(segment["flight_number"], "ZZ101")
        self.assertEqual(
            segment["operating_carrier"]["name"],
            "Duffel Airways",
        )
        self.assertNotIn("passengers", result["offers"][0])


class DuffelClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_credentials_fail_before_network_access(self):
        client = duffel.DuffelClient(
            access_token="",
            max_retries=0,
        )
        with self.assertRaisesRegex(duffel.DuffelError, "not configured"):
            await client.request(method="GET", path="/test")

    async def test_credential_mode_does_not_expose_token(self):
        client = duffel.DuffelClient(access_token="duffel_test_private")
        self.assertEqual(client.credential_mode, "test")
        self.assertNotIn("private", client.credential_mode)

    async def test_concurrency_is_bounded(self):
        client = duffel.DuffelClient(
            access_token="duffel_test_token",
            base_url="https://example.test",
            max_retries=0,
            max_inflight=2,
        )
        counter_lock = threading.Lock()
        counters = {
            "active": 0,
            "peak": 0,
            "api_calls": 0,
        }

        def fake_send_json_sync(**kwargs):
            with counter_lock:
                counters["active"] += 1
                counters["peak"] = max(counters["peak"], counters["active"])
                counters["api_calls"] += 1
            time.sleep(0.02)
            with counter_lock:
                counters["active"] -= 1
            return {"data": []}

        client._send_json_sync = fake_send_json_sync
        await asyncio.gather(
            *(
                client.request(method="GET", path="/places/suggestions")
                for _ in range(8)
            )
        )

        self.assertEqual(counters["api_calls"], 8)
        self.assertLessEqual(counters["peak"], 2)

    async def test_search_uses_bounded_supplier_timeout(self):
        client = duffel.DuffelClient(
            access_token="duffel_test_token",
            timeout_seconds=10,
            supplier_timeout_ms=60_000,
            max_retries=0,
        )
        captured = []

        def fake_send_json_sync(**kwargs):
            captured.append(kwargs)
            if kwargs["method"] == "POST":
                return {"data": {"id": "orq_test"}}
            return {"data": []}

        client._send_json_sync = fake_send_json_sync
        await client.search_flights(
            {"data": {"max_connections": 1}},
            max_results=8,
        )

        self.assertEqual(captured[0]["query"]["supplier_timeout"], 9_000)
        self.assertEqual(captured[0]["query"]["return_offers"], "false")
        self.assertEqual(captured[0]["query"]["view"], "offers")
        self.assertEqual(captured[1]["path"], "/air/offers")
        self.assertEqual(captured[1]["query"]["offer_request_id"], "orq_test")
        self.assertEqual(captured[1]["query"]["limit"], 8)
        self.assertEqual(captured[1]["query"]["sort"], "total_amount")


if __name__ == "__main__":
    unittest.main()
