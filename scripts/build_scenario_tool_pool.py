#!/usr/bin/env python3
"""Build the static scenario-generation tool pool from MCP discovery output.

The live MCP catalog remains the source of truth.  Scenario generation uses a
snapshot so thousands of generation workers do not have to start every MCP
server merely to read tool descriptions and JSON schemas.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DISCOVERY = (
    PROJECT_DIR / "config/scenario_tool_discovery_base.json"
)
DEFAULT_DOMAINS = PROJECT_DIR / "config/domains.json"
DEFAULT_OUTPUT = PROJECT_DIR / "config/scenario_tool_pool.json"
DEFAULT_SUPPLEMENT = (
    PROJECT_DIR / "config/scenario_tool_discovery_extensions.json"
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def merge_discoveries(discoveries: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge snapshots by server name, preferring the newest occurrence."""
    by_server: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for discovery in discoveries:
        for server in discovery.get("results", []):
            server_name = str(server.get("name", ""))
            if not server_name:
                continue
            if server_name not in by_server:
                order.append(server_name)
            by_server[server_name] = server
    return {"results": [by_server[name] for name in order]}


def build_tool_pool(
    discovery: dict[str, Any],
    domains_config: dict[str, Any],
) -> dict[str, Any]:
    server_domains: dict[str, list[str]] = defaultdict(list)
    allowlists: dict[tuple[str, str], set[str]] = {}
    for domain, config in domains_config.items():
        selected = list(
            dict.fromkeys(
                [
                    *config.get("core_servers", []),
                    *config.get("optional_servers", []),
                ]
            )
        )
        raw_allowlists = config.get("tool_allowlists", {})
        for server_name in selected:
            server_domains[server_name].append(domain)
            if server_name in raw_allowlists:
                allowlists[(domain, server_name)] = set(
                    raw_allowlists[server_name]
                )

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for server in discovery.get("results", []):
        server_name = str(server.get("name", ""))
        if not server.get("connected") or server_name not in server_domains:
            continue
        for raw_tool in server.get("tools", []):
            tool_name = str(raw_tool.get("name", ""))
            if not tool_name:
                continue
            supported_domains = [
                domain
                for domain in server_domains[server_name]
                if (
                    (domain, server_name) not in allowlists
                    or tool_name in allowlists[(domain, server_name)]
                )
            ]
            if not supported_domains:
                continue
            key = (server_name, tool_name)
            current = by_key.get(key)
            if current is None:
                current = {
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "qualified_name": (
                        raw_tool.get("qualified_name")
                        or f"{server_name}:{tool_name}"
                    ),
                    "description": str(raw_tool.get("description") or ""),
                    "input_schema": raw_tool.get("input_schema") or {},
                    "domains": [],
                }
                by_key[key] = current
            current["domains"] = sorted(
                set(current["domains"]).union(supported_domains)
            )

    tools = sorted(
        by_key.values(),
        key=lambda item: (item["server_name"], item["tool_name"]),
    )
    domain_counts = {
        domain: sum(domain in tool["domains"] for tool in tools)
        for domain in domains_config
    }
    return {
        "schema_version": "1.0",
        "source": "MCP tools/list discovery snapshot",
        "tool_count": len(tools),
        "domain_tool_counts": domain_counts,
        "tools": tools,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument(
        "--supplement",
        action="append",
        type=Path,
        default=None,
        help="Additional tools/list snapshot; later snapshots replace a server.",
    )
    parser.add_argument("--domains", type=Path, default=DEFAULT_DOMAINS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    supplement_paths = (
        args.supplement
        if args.supplement is not None
        else ([DEFAULT_SUPPLEMENT] if DEFAULT_SUPPLEMENT.is_file() else [])
    )
    discovery_paths = [args.discovery.resolve(), *(
        path.resolve() for path in supplement_paths
    )]
    tool_pool = build_tool_pool(
        merge_discoveries([load_json(path) for path in discovery_paths]),
        load_json(args.domains.resolve()),
    )
    tool_pool["source_artifacts"] = [
        str(path.relative_to(PROJECT_DIR))
        if path.is_relative_to(PROJECT_DIR)
        else str(path)
        for path in discovery_paths
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(tool_pool, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "tool_count": tool_pool["tool_count"],
                "domain_tool_counts": tool_pool["domain_tool_counts"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
