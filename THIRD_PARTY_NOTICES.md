# Third-party software notices

PrivacyTrace evaluates real MCP servers. `mcp_servers/manifest.json` is the
machine-readable source of truth for server names, upstream URLs, revisions,
installation modes, and local snapshot hashes.

## Included licensed snapshots

The release retains the original license files for BioMCP, Call for Papers,
Context7, DailyMed MCP, Hugging Face MCP, Geoapify MCP, Reddit MCP, National
Parks MCP, Metropolitan Museum MCP, Paper Search MCP, and Wikipedia
MCP. Their upstream authors and copyright holders are unrelated to the
anonymous paper authors.

The Duffel search-only server and the stateless remote adapters are
project-maintained review artifacts and are covered by `REVIEW_LICENSE.md`.

## Referenced but not redistributed

At the pinned experimental revisions, the following upstream repositories did
not contain an explicit license file. Their source is therefore not included:

- Car Price Evaluator — `yusaaztrk/car-price-mcp-main`
- FruityVice — `CelalKhalilov/fruityvice-mcp`
- Game Trends — `halismertkir/game-trends-mcp`
- Medical Calculator — `vitaldb/medcalc`
- Movie Recommender — `iremert/movie-recommender-mcp`
- Time MCP — `dumyCq/time-mcp`
- Weather Data — `HarunGuclu/weather_mcp`

`mcp_servers/install.py` can retrieve the pinned public revisions only after an
explicit `--accept-unlicensed-upstream` acknowledgement. Retrieval does not
grant a license; reviewers are responsible for complying with upstream terms.

## Remotely hosted services

OneBusAway, tickadoo, Open Food Facts, OpenFDA, Remoote Jobs, and Keenable Web
Search are accessed through stateless adapters. Their service terms and data
policies continue to apply.
