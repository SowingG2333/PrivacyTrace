#!/usr/bin/env python3
"""Discover live MCP tool metadata for scenario-pool snapshots."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def domain_server_policy(
    domains_config: dict[str, Any],
) -> tuple[list[str], dict[str, set[str] | None]]:
    servers: list[str] = []
    policy: dict[str, set[str] | None] = {}
    for config in domains_config.values():
        selected = [
            *config.get("core_servers", []),
            *config.get("optional_servers", []),
        ]
        allowlists = config.get("tool_allowlists", {})
        for server_name in selected:
            if server_name not in servers:
                servers.append(server_name)
            allowed = allowlists.get(server_name)
            if allowed is None:
                policy[server_name] = None
            elif policy.get(server_name, set()) is not None:
                policy.setdefault(server_name, set()).update(allowed)
    return servers, policy


async def discover_server(
    server_name: str,
    *,
    allowlist: set[str] | None,
) -> dict[str, Any]:
    from pydantic_ai.models.test import TestModel

    from agent_env.execution_limits import ExecutionLimits
    from agent_env.pydantic_agent import PydanticAIAgentAdapter

    adapter = PydanticAIAgentAdapter(
        base_url="http://unused.test/v1",
        api_key="discovery-only",
        model_name="discovery-only",
        model=TestModel(call_tools=[], custom_output_text="ready"),
        server_names=[server_name],
        execution_limits=ExecutionLimits(
            max_rounds=1,
            max_tool_calls=1,
        ),
        project_dir=PROJECT_DIR,
    )
    try:
        await adapter.initialize()
        tools = adapter.discovered_tools()[server_name]
        if allowlist is not None:
            tools = [tool for tool in tools if tool["name"] in allowlist]
        return {
            "name": server_name,
            "transport": "mcp",
            "connected": True,
            "tool_count": len(tools),
            "tools": tools,
            "connect_error": None,
        }
    except Exception as exc:
        return {
            "name": server_name,
            "transport": "mcp",
            "connected": False,
            "tool_count": 0,
            "tools": [],
            "connect_error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        await adapter.close()


async def discover(args: argparse.Namespace) -> dict[str, Any]:
    domains = load_json(args.domains.resolve())
    domain_servers, allowlists = domain_server_policy(domains)
    selected = args.servers or domain_servers
    unknown = set(selected) - set(domain_servers)
    if unknown:
        raise ValueError(
            f"Servers are not selected by any scenario domain: {sorted(unknown)}"
        )
    results: list[dict[str, Any]] = []
    for server_name in selected:
        result = await discover_server(
            server_name,
            allowlist=(
                allowlists.get(server_name)
                if args.respect_domain_allowlists
                else None
            ),
        )
        results.append(result)
        print(
            f"{server_name}: connected={result['connected']} "
            f"tools={result['tool_count']}",
            flush=True,
        )
    return {
        "schema_version": "1.0",
        "source": "live MCP tools/list through PydanticAI/FastMCP",
        "results": results,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--domains",
        type=Path,
        default=PROJECT_DIR / "config/domains.json",
    )
    parser.add_argument("--servers", nargs="+")
    parser.add_argument(
        "--respect-domain-allowlists",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    return parser


def main() -> int:
    from agent_env.settings import load_project_env

    load_project_env(PROJECT_DIR)
    args = build_arg_parser().parse_args()
    try:
        payload = asyncio.run(discover(args))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    failed = [
        result["name"]
        for result in payload["results"]
        if not result["connected"]
    ]
    print(
        json.dumps(
            {
                "output": str(args.output),
                "server_count": len(payload["results"]),
                "failed_servers": failed,
                "tool_count": sum(
                    result["tool_count"] for result in payload["results"]
                ),
            }
        )
    )
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
