# Server migration bundle

This project stores persisted provenance and recovery-contract input keys as
paths relative to the project root. The checkout may therefore be unpacked at
any server path, provided commands are launched from the project root.

## Included

- Source code, configuration, tests, and documentation.
- All generated `artifacts/` and `data/` content.
- MCP source trees and locally generated MCP data, excluding reinstallable
  dependency/cache directories.
- The partially completed first Semantic Judge, including all batch outputs
  and its unresolved-failure ledger.

Secrets (`.env` and `.env.mcp`) are intentionally excluded. Transfer them over
an authenticated encrypted channel and set mode `600` on the server.

## Install and resume

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
chmod 600 .env
./scripts/resume_server_judge.sh
```

The resume contract is: direct transport with proxy-environment inheritance
disabled, equivalent GLM pool (slots 1/2 `glm-5.2`, slot 3 `glm5-2`), per-key
RPM `500,100,1000`, 50 workers, global RPM 1600, temperature 0, service-default
output limit, five request retries, and five recovery rounds. Only the first
Judge pass is launched.
