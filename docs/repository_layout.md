# Repository layout

The repository is organized by ownership and artifact lifetime.

## Source packages

| Path | Responsibility |
|---|---|
| `privacy_trace/` | Personal-data synthesis, natural-language scenarios with separate generation traces, privacy attacks, and leakage analysis |
| `agent_env/` | Task models, simulator, PydanticAI adapter, MCP process lifecycle, orchestration, and trajectory evaluation |
| `llm/` | OpenAI-compatible provider configuration and request normalization |

Source packages never import from `scripts/` or `artifacts/`.

## MCP catalog

`mcp_servers/commands.json` is the launch catalog. `mcp_servers/` contains the
vendored server sources and the pinned adapters used by that catalog.
Generated `.venv`, `node_modules`, `build`, `dist`, and cache directories are
recreated by `mcp_servers/install.sh` and are not source.

## Configuration and reproducibility inputs

- `config/` contains runtime policy and MCP tool snapshots, never credentials.
- `data/targets/` contains calibrated demographic targets.
- `data/catalogs/` contains reproducible lookup catalogs.
- `data/sources/` contains the compact official-source derivatives required to
  rebuild targets.
- `.env` and `.env.mcp` contain local credentials and are ignored.

## Local artifacts

`artifacts/profile_pool/` is the one retained local dataset. Other children of
`artifacts/` are disposable experiment runs:

```text
artifacts/
├── profile_pool/           # retained candidate pool and 1k sampled profiles
├── profile_runs/           # newly generated profile runs
├── scenario/               # generated/audited scenarios
├── trajectories/           # Agent trajectories and batch summaries
└── privacy_attacks/        # inferred profiles and evaluation outputs
```

No source module depends on a diagnostic or report artifact. If a snapshot is
required to rebuild configuration, it belongs in `config/`, as with
`scenario_tool_discovery_base.json`.

## Entry points

Operational commands live in `scripts/`. Reusable logic remains in source
packages, so importing a module does not start a server or mutate artifacts.
See `scripts/README.md` for commands grouped by workflow.

## Dependency direction

```text
scripts
  ├─ privacy_trace
  └─ agent_env
       ├─ llm
       ├─ PydanticAI / FastMCP
       └─ mcp_servers/commands.json

config + data → source packages → artifacts
```

The custom MCP-Bench executor, server managers, benchmark runner, task
synthesis package, and bundled benchmark task data are intentionally absent.
PydanticAI is the only evaluated Agent runtime.
