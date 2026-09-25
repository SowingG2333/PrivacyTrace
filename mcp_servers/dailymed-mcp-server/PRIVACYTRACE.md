# PrivacyTrace DailyMed MCP

This directory is a trimmed vendoring of
[`RowanErasmus/dailymed-mcp-server`](https://github.com/RowanErasmus/dailymed-mcp-server)
at commit `4a432da6ab4599b225567647fd9cf511efac2968`.

PrivacyTrace intentionally exposes only six read-only tools:

- `search_spls`
- `search_drug_names`
- `get_drug_details`
- `get_drug_history`
- `get_drug_ndcs`
- `get_drug_packaging`

The high-fan-out shorthand `search_spls.query` path is disabled; callers use
DailyMed's direct `drug_name` filter instead. Upstream's large optional mapping
snapshots are also omitted. This keeps the experiment reproducible and avoids
the timeout-prone query expansion while preserving official DailyMed API
lookups.
