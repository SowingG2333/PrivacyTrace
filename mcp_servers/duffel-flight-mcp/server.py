"""Read-only flight-search MCP tools backed by the Duffel Flights API."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mcp.server.fastmcp import FastMCP


DEFAULT_BASE_URL = "https://api.duffel.com"
DEFAULT_API_VERSION = "v2"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_SUPPLIER_TIMEOUT_MS = 15_000
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_INFLIGHT = 4
MAX_TOOL_RESULTS = 20
IATA_PATTERN = re.compile(r"^[A-Z]{3}$")
CABIN_CLASSES = {
    "economy",
    "premium_economy",
    "business",
    "first",
}


class DuffelError(RuntimeError):
    """Safe error propagated to MCP tool output."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _api_error_message(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        detail = first.get("message") or first.get("title") or first.get("code")
        if detail:
            return str(detail)[:500]
    if payload.get("message"):
        return str(payload["message"])[:500]
    return fallback


def _validate_iata(value: str, field: str) -> str:
    normalized = value.strip().upper()
    if not IATA_PATTERN.fullmatch(normalized):
        raise DuffelError(f"{field} must be a three-letter IATA code")
    return normalized


def _validate_iso_date(value: str, field: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DuffelError(f"{field} must use YYYY-MM-DD format") from exc
    if parsed < date.today():
        raise DuffelError(f"{field} must not be in the past")
    return parsed.isoformat()


def build_flight_request(
    *,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
    children: int,
    infants: int,
    cabin_class: str,
    non_stop: bool,
    max_connections: int,
    max_results: int,
) -> dict[str, Any]:
    """Validate MCP inputs and map them to a Duffel offer request."""
    normalized_origin = _validate_iata(origin, "origin")
    normalized_destination = _validate_iata(destination, "destination")
    if normalized_origin == normalized_destination:
        raise DuffelError("origin and destination must differ")

    normalized_departure = _validate_iso_date(departure_date, "departure_date")
    normalized_return = (
        _validate_iso_date(return_date, "return_date") if return_date else None
    )
    if normalized_return and normalized_return < normalized_departure:
        raise DuffelError("return_date must not be before departure_date")

    if not 1 <= adults <= 9:
        raise DuffelError("adults must be between 1 and 9")
    if not 0 <= children <= 9:
        raise DuffelError("children must be between 0 and 9")
    if not 0 <= infants <= 9:
        raise DuffelError("infants must be between 0 and 9")
    if infants > adults:
        raise DuffelError("infants must not exceed adults")
    if adults + children + infants > 9:
        raise DuffelError("total passengers must not exceed 9")

    normalized_class = cabin_class.strip().lower()
    if normalized_class not in CABIN_CLASSES:
        raise DuffelError(
            "cabin_class must be economy, premium_economy, business, or first"
        )
    if not 0 <= max_connections <= 3:
        raise DuffelError("max_connections must be between 0 and 3")
    if not 1 <= max_results <= MAX_TOOL_RESULTS:
        raise DuffelError(
            f"max_results must be between 1 and {MAX_TOOL_RESULTS}"
        )

    slices = [
        {
            "origin": normalized_origin,
            "destination": normalized_destination,
            "departure_date": normalized_departure,
        }
    ]
    if normalized_return:
        slices.append(
            {
                "origin": normalized_destination,
                "destination": normalized_origin,
                "departure_date": normalized_return,
            }
        )

    passengers = (
        [{"type": "adult"} for _ in range(adults)]
        + [{"type": "child"} for _ in range(children)]
        + [{"type": "infant_without_seat"} for _ in range(infants)]
    )
    return {
        "body": {
            "data": {
                "slices": slices,
                "passengers": passengers,
                "cabin_class": normalized_class,
                "max_connections": 0 if non_stop else max_connections,
            }
        },
        "max_results": max_results,
        "input": {
            "origin": normalized_origin,
            "destination": normalized_destination,
            "departure_date": normalized_departure,
            "return_date": normalized_return,
            "adults": adults,
            "children": children,
            "infants": infants,
            "cabin_class": normalized_class,
            "non_stop": non_stop,
            "max_connections": 0 if non_stop else max_connections,
        },
    }


def build_place_query(*, keyword: str, max_results: int) -> dict[str, Any]:
    normalized_keyword = keyword.strip()
    if not normalized_keyword or len(normalized_keyword) > 80:
        raise DuffelError("keyword must contain between 1 and 80 characters")
    if not re.fullmatch(r"[\w\s./:'(),&+-]+", normalized_keyword):
        raise DuffelError("keyword contains unsupported characters")
    if not 1 <= max_results <= MAX_TOOL_RESULTS:
        raise DuffelError(
            f"max_results must be between 1 and {MAX_TOOL_RESULTS}"
        )
    return {
        "query": normalized_keyword,
        "_max_results": max_results,
    }


class DuffelClient:
    """Minimal async client with retries and bounded concurrency."""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
        timeout_seconds: float | None = None,
        supplier_timeout_ms: int | None = None,
        max_retries: int | None = None,
        max_inflight: int | None = None,
    ) -> None:
        self.access_token = (
            access_token
            if access_token is not None
            else os.getenv("DUFFEL_ACCESS_TOKEN")
        )
        self.base_url = (
            base_url
            if base_url is not None
            else os.getenv("DUFFEL_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        if not self.base_url.startswith(("https://", "http://")):
            raise ValueError("DUFFEL_BASE_URL must use http or https")
        self.api_version = (
            api_version
            if api_version is not None
            else os.getenv("DUFFEL_API_VERSION", DEFAULT_API_VERSION)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _env_float(
                "DUFFEL_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
                3.0,
                120.0,
            )
        )
        configured_supplier_timeout = (
            supplier_timeout_ms
            if supplier_timeout_ms is not None
            else _env_int(
                "DUFFEL_SUPPLIER_TIMEOUT_MS",
                DEFAULT_SUPPLIER_TIMEOUT_MS,
                2_000,
                60_000,
            )
        )
        http_budget_ms = max(int(self.timeout_seconds * 1_000) - 1_000, 2_000)
        self.supplier_timeout_ms = min(
            configured_supplier_timeout,
            http_budget_ms,
            60_000,
        )
        self.max_retries = (
            max_retries
            if max_retries is not None
            else _env_int(
                "DUFFEL_MAX_RETRIES",
                DEFAULT_MAX_RETRIES,
                0,
                5,
            )
        )
        inflight = (
            max_inflight
            if max_inflight is not None
            else _env_int(
                "DUFFEL_MAX_INFLIGHT",
                DEFAULT_MAX_INFLIGHT,
                1,
                32,
            )
        )
        self._request_semaphore = asyncio.Semaphore(inflight)

    @property
    def credentials_available(self) -> bool:
        return bool(self.access_token)

    @property
    def credential_mode(self) -> str:
        token = self.access_token or ""
        if token.startswith("duffel_test_"):
            return "test"
        if token.startswith("duffel_live_"):
            return "live"
        return "unknown"

    def _require_credentials(self) -> None:
        if not self.credentials_available:
            raise DuffelError(
                "Duffel credentials are not configured; set DUFFEL_ACCESS_TOKEN"
            )

    def _send_json_sync(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query, doseq=True)}"
        body = (
            json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            if json_body is not None
            else None
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "Duffel-Version": self.api_version,
            "User-Agent": "PrivacyTrace-Duffel-MCP/1.0",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                raw = exc.read(16_384).decode("utf-8", errors="replace")
                payload = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                payload = None
            raise DuffelError(
                _api_error_message(payload, f"Duffel returned HTTP {exc.code}"),
                status_code=exc.code,
                retryable=exc.code in {408, 429} or exc.code >= 500,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            raise DuffelError(
                f"Duffel network error: {reason or exc}",
                retryable=True,
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DuffelError(
                "Duffel returned invalid JSON",
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise DuffelError(
                "Duffel returned an unexpected response",
                retryable=True,
            )
        return payload

    async def _send_json(self, **kwargs: Any) -> dict[str, Any]:
        async with self._request_semaphore:
            return await asyncio.to_thread(self._send_json_sync, **kwargs)

    async def request(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_credentials()
        for attempt in range(self.max_retries + 1):
            try:
                return await self._send_json(
                    method=method,
                    path=path,
                    query=query,
                    json_body=json_body,
                )
            except DuffelError as exc:
                if exc.retryable and attempt < self.max_retries:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                raise
        raise DuffelError("Duffel request failed after retries", retryable=True)

    async def search_flights(
        self,
        body: dict[str, Any],
        *,
        max_results: int,
    ) -> dict[str, Any]:
        offer_request = await self.request(
            method="POST",
            path="/air/offer_requests",
            query={
                "return_offers": "false",
                "supplier_timeout": self.supplier_timeout_ms,
                "view": "offers",
            },
            json_body=body,
        )
        request_data = offer_request.get("data")
        if not isinstance(request_data, dict):
            raise DuffelError("Duffel offer request response did not contain data")
        offer_request_id = request_data.get("id")
        if not isinstance(offer_request_id, str) or not offer_request_id:
            raise DuffelError(
                "Duffel offer request response did not contain an id"
            )
        search_data = body.get("data")
        max_connections = (
            search_data.get("max_connections", 1)
            if isinstance(search_data, dict)
            else 1
        )
        offers_response = await self.request(
            method="GET",
            path="/air/offers",
            query={
                "offer_request_id": offer_request_id,
                "limit": max_results,
                "sort": "total_amount",
                "max_connections": max_connections,
            },
        )
        offers = offers_response.get("data")
        if not isinstance(offers, list):
            raise DuffelError("Duffel offers response did not contain a list")
        return {
            **offer_request,
            "data": {
                **request_data,
                "offers": offers,
            },
        }

    async def suggest_places(self, query: dict[str, Any]) -> dict[str, Any]:
        return await self.request(
            method="GET",
            path="/places/suggestions",
            query=query,
        )


def _compact_place(place: Any) -> dict[str, Any] | None:
    if not isinstance(place, dict):
        return None
    return {
        "id": place.get("id"),
        "type": place.get("type"),
        "name": place.get("name"),
        "iata_code": place.get("iata_code"),
        "iata_city_code": place.get("iata_city_code"),
        "city_name": place.get("city_name"),
        "country_code": place.get("iata_country_code"),
        "time_zone": place.get("time_zone"),
    }


def _compact_carrier(carrier: Any) -> dict[str, Any] | None:
    if not isinstance(carrier, dict):
        return None
    return {
        "name": carrier.get("name"),
        "iata_code": carrier.get("iata_code"),
    }


def _normalize_segment(segment: dict[str, Any]) -> dict[str, Any]:
    marketing = segment.get("marketing_carrier")
    operating = segment.get("operating_carrier")
    flight_number = segment.get("marketing_carrier_flight_number")
    if not flight_number and isinstance(marketing, dict):
        carrier_code = marketing.get("iata_code")
        number = segment.get("flight_number")
        if carrier_code and number:
            flight_number = f"{carrier_code}{number}"
    return {
        "origin": _compact_place(segment.get("origin")),
        "destination": _compact_place(segment.get("destination")),
        "departing_at": segment.get("departing_at"),
        "arriving_at": segment.get("arriving_at"),
        "duration": segment.get("duration"),
        "flight_number": flight_number,
        "marketing_carrier": _compact_carrier(marketing),
        "operating_carrier": _compact_carrier(operating),
        "aircraft": {
            "name": segment.get("aircraft", {}).get("name"),
            "iata_code": segment.get("aircraft", {}).get("iata_code"),
        }
        if isinstance(segment.get("aircraft"), dict)
        else None,
        "technical_stops": len(segment.get("stops", []))
        if isinstance(segment.get("stops"), list)
        else 0,
    }


def normalize_flight_response(
    payload: dict[str, Any],
    request_spec: dict[str, Any],
    *,
    credential_mode: str = "unknown",
) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    raw_offers = data.get("offers", [])
    if not isinstance(raw_offers, list):
        raw_offers = []

    offers = []
    for offer in raw_offers[: int(request_spec["max_results"])]:
        if not isinstance(offer, dict):
            continue
        slices = []
        for journey_slice in offer.get("slices", []):
            if not isinstance(journey_slice, dict):
                continue
            segments = journey_slice.get("segments", [])
            if not isinstance(segments, list):
                segments = []
            compact_segments = [
                _normalize_segment(segment)
                for segment in segments
                if isinstance(segment, dict)
            ]
            slices.append(
                {
                    "origin": _compact_place(journey_slice.get("origin")),
                    "destination": _compact_place(
                        journey_slice.get("destination")
                    ),
                    "departing_at": (
                        compact_segments[0].get("departing_at")
                        if compact_segments
                        else None
                    ),
                    "arriving_at": (
                        compact_segments[-1].get("arriving_at")
                        if compact_segments
                        else None
                    ),
                    "duration": journey_slice.get("duration"),
                    "connections": max(len(compact_segments) - 1, 0),
                    "segments": compact_segments,
                }
            )
        payment = offer.get("payment_requirements")
        offers.append(
            {
                "id": offer.get("id"),
                "live_mode": offer.get("live_mode"),
                "expires_at": offer.get("expires_at"),
                "owner": _compact_carrier(offer.get("owner")),
                "price": {
                    "currency": offer.get("total_currency"),
                    "total": offer.get("total_amount"),
                    "tax": offer.get("tax_amount"),
                },
                "requires_instant_payment": (
                    payment.get("requires_instant_payment")
                    if isinstance(payment, dict)
                    else None
                ),
                "total_emissions_kg": offer.get("total_emissions_kg"),
                "slices": slices,
            }
        )

    return {
        "query": request_spec["input"],
        "offer_request_id": data.get("id"),
        "count": len(offers),
        "offers": offers,
        "credential_mode": credential_mode,
        "source": "Duffel Flights API",
        "search_only": True,
        "booking_capability_exposed": False,
    }


def normalize_place_response(
    payload: dict[str, Any],
    query: dict[str, Any],
    *,
    credential_mode: str = "unknown",
) -> dict[str, Any]:
    raw_places = payload.get("data", [])
    if not isinstance(raw_places, list):
        raw_places = []
    max_results = int(query["_max_results"])
    places = [
        compact
        for item in raw_places[:max_results]
        if (compact := _compact_place(item)) is not None
    ]
    return {
        "keyword": query["query"],
        "count": len(places),
        "places": places,
        "credential_mode": credential_mode,
        "source": "Duffel Place Suggestions API",
        "search_only": True,
    }


def _tool_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, DuffelError):
        return {
            "error": str(exc),
            "retryable": exc.retryable,
            "status_code": exc.status_code,
            "source": "Duffel Flights API",
        }
    return {
        "error": f"Unexpected Duffel client error: {type(exc).__name__}",
        "retryable": False,
        "source": "Duffel Flights API",
    }


client = DuffelClient()
mcp = FastMCP("duffel-flight-mcp")


@mcp.tool()
async def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin_class: str = "economy",
    non_stop: bool = False,
    max_connections: int = 1,
    max_results: int = 8,
) -> dict[str, Any]:
    """Search Duffel flight offers without booking, holding, or purchasing.

    Origin and destination must be three-letter IATA airport or city codes.
    Dates use YYYY-MM-DD. Test tokens return sandbox schedules and prices.
    """
    try:
        request_spec = build_flight_request(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            adults=adults,
            children=children,
            infants=infants,
            cabin_class=cabin_class,
            non_stop=non_stop,
            max_connections=max_connections,
            max_results=max_results,
        )
        payload = await client.search_flights(
            request_spec["body"],
            max_results=request_spec["max_results"],
        )
        return normalize_flight_response(
            payload,
            request_spec,
            credential_mode=client.credential_mode,
        )
    except Exception as exc:
        return _tool_error(exc)


@mcp.tool()
async def search_airports(
    keyword: str,
    max_results: int = 8,
) -> dict[str, Any]:
    """Suggest Duffel airports and cities by name, country, or IATA code."""
    try:
        query = build_place_query(keyword=keyword, max_results=max_results)
        api_query = {"query": query["query"]}
        payload = await client.suggest_places(api_query)
        return normalize_place_response(
            payload,
            query,
            credential_mode=client.credential_mode,
        )
    except Exception as exc:
        return _tool_error(exc)


if __name__ == "__main__":
    mcp.run(transport="stdio")
