import assert from "node:assert/strict";
import test from "node:test";
import { GeoapifyMapsTools } from "../src/services/toolclass.js";

interface RecordedCall {
  url: URL;
  init?: RequestInit;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function createMockFetch(
  handler: (call: RecordedCall) => Response | Promise<Response>
): { calls: RecordedCall[]; fetchImpl: typeof fetch } {
  const calls: RecordedCall[] = [];
  const fetchImpl = (async (input: string | URL | Request, init?: RequestInit) => {
    const call = { url: new URL(String(input)), init };
    calls.push(call);
    return handler(call);
  }) as typeof fetch;
  return { calls, fetchImpl };
}

test("requires a Geoapify API key", () => {
  assert.throws(() => new GeoapifyMapsTools({ apiKey: "" }), /GEOAPIFY_API_KEY/);
});

test("geocode calls Geoapify and normalizes the legacy output shape", async () => {
  const mock = createMockFetch(() => jsonResponse({
    results: [{
      lat: 31.2304,
      lon: 121.4737,
      formatted: "上海市，中国",
      place_id: "shanghai-id",
    }],
  }));
  const tools = new GeoapifyMapsTools({ apiKey: "test-key", fetchImpl: mock.fetchImpl });

  const result = await tools.geocode("上海");

  assert.deepEqual(result.location, { lat: 31.2304, lng: 121.4737 });
  assert.equal(result.place_id, "shanghai-id");
  assert.equal(mock.calls[0].url.pathname, "/v1/geocode/search");
  assert.equal(mock.calls[0].url.searchParams.get("text"), "上海");
  assert.equal(mock.calls[0].url.searchParams.get("apiKey"), "test-key");
});

test("nearby restaurant search uses Places API and reports unsupported Google fields honestly", async () => {
  const mock = createMockFetch(() => jsonResponse({
    features: [{
      properties: {
        name: "示例餐厅",
        place_id: "restaurant-id",
        formatted: "上海市黄浦区",
        lat: 31.23,
        lon: 121.47,
        categories: ["catering.restaurant"],
        distance: 125,
      },
    }],
  }));
  const tools = new GeoapifyMapsTools({ apiKey: "test-key", fetchImpl: mock.fetchImpl });

  const places = await tools.searchNearbyPlaces({
    location: { lat: 31.23, lng: 121.47 },
    keyword: "餐厅",
    minRating: 4.5,
    openNow: true,
  });

  assert.equal(mock.calls[0].url.pathname, "/v2/places");
  assert.equal(mock.calls[0].url.searchParams.get("categories"), "catering.restaurant");
  assert.equal(places[0].name, "示例餐厅");
  assert.equal(places[0].rating, null);
  assert.equal(places[0].opening_hours.open_now, null);
  assert.match(places[0].provider_note ?? "", /does not provide/);
});

test("unknown nearby keywords fall back to filtered amenity geocoding", async () => {
  const mock = createMockFetch(() => jsonResponse({
    results: [{ name: "Unique Shop", place_id: "shop-id", formatted: "Somewhere", lat: 1, lon: 2 }],
  }));
  const tools = new GeoapifyMapsTools({ apiKey: "test-key", fetchImpl: mock.fetchImpl });

  const places = await tools.searchNearbyPlaces({
    location: { lat: 1, lng: 2 },
    keyword: "Unique Shop",
    radius: 500,
  });

  assert.equal(mock.calls[0].url.pathname, "/v1/geocode/search");
  assert.equal(mock.calls[0].url.searchParams.get("type"), "amenity");
  assert.equal(mock.calls[0].url.searchParams.get("filter"), "circle:2,1,500");
  assert.equal(places[0].place_id, "shop-id");
});

test("distance matrix preserves matrix shape and sends longitude-latitude locations", async () => {
  const mock = createMockFetch(() => jsonResponse({
    sources_to_targets: [[{ distance: 1234, time: 456 }]],
  }));
  const tools = new GeoapifyMapsTools({ apiKey: "test-key", fetchImpl: mock.fetchImpl });

  const result = await tools.calculateDistanceMatrix(["31.2,121.4"], ["31.3,121.5"], "driving");

  assert.equal(mock.calls[0].url.pathname, "/v1/routematrix");
  assert.equal(mock.calls[0].init?.method, "POST");
  const body = JSON.parse(String(mock.calls[0].init?.body));
  assert.deepEqual(body.sources[0].location, [121.4, 31.2]);
  assert.deepEqual(result.distances, [[{ value: 1234, text: "1.2 km" }]]);
  assert.deepEqual(result.durations, [[{ value: 456, text: "8 min" }]]);
});

test("directions maps travel mode and calculates legacy distance and time fields", async () => {
  const mock = createMockFetch(() => jsonResponse({
    results: [{
      distance: 2.5,
      distance_units: "kilometers",
      time: 600,
      legs: [{ steps: [] }],
    }],
  }));
  const tools = new GeoapifyMapsTools({ apiKey: "test-key", fetchImpl: mock.fetchImpl });
  const departure = new Date("2026-08-04T00:00:00.000Z");

  const result = await tools.getDirections("31.2,121.4", "31.3,121.5", "walking", departure);

  assert.equal(mock.calls[0].url.pathname, "/v1/routing");
  assert.equal(mock.calls[0].url.searchParams.get("mode"), "walk");
  assert.deepEqual(result.total_distance, { value: 2500, text: "2.5 km" });
  assert.deepEqual(result.total_duration, { value: 600, text: "10 min" });
  assert.equal(result.departure_time, "2026-08-04T00:00:00.000Z");
  assert.equal(result.arrival_time, "2026-08-04T00:10:00.000Z");
  assert.match(result.provider_note, /does not use departure_time/);
});

test("elevation uses the Geoapify batch endpoint", async () => {
  const mock = createMockFetch(() => jsonResponse({
    results: [{ location: { lat: 31.2, lon: 121.4 }, elevation: 8 }],
  }));
  const tools = new GeoapifyMapsTools({ apiKey: "test-key", fetchImpl: mock.fetchImpl });

  const result = await tools.getElevation([{ latitude: 31.2, longitude: 121.4 }]);

  assert.equal(mock.calls[0].url.pathname, "/v1/geodata/elevation");
  assert.deepEqual(result[0].location, { lat: 31.2, lng: 121.4 });
  assert.equal(result[0].elevation, 8);
});
