# Duffel Flight MCP

A small search-only MCP server for the Duffel Flights API.

## Tools

- `search_flights`: creates a Duffel Offer Request and returns compact,
  bounded flight offers.
- `search_airports`: finds airport and city suggestions through Duffel Places.

The server deliberately exposes no order, payment, booking, hold, cancellation,
or reservation mutation tools.

## Configuration

Required:

- `DUFFEL_ACCESS_TOKEN`

Create the token in the Duffel dashboard. Test tokens start with
`duffel_test_`; live tokens start with `duffel_live_`. Test mode is suitable
for integration testing, but its schedules and prices are not realistic.

Optional:

- `DUFFEL_BASE_URL` (defaults to `https://api.duffel.com`)
- `DUFFEL_API_VERSION` (defaults to `v2`)
- `DUFFEL_TIMEOUT_SECONDS` (defaults to `30`)
- `DUFFEL_SUPPLIER_TIMEOUT_MS` (defaults to `15000`)
- `DUFFEL_MAX_RETRIES` (defaults to `2`)
- `DUFFEL_MAX_INFLIGHT` (defaults to `4`)
