#!/usr/bin/env python3
"""Expose one catalog MCP server as a reusable loopback HTTP endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-name", required=True)
    parser.add_argument(
        "--commands",
        type=Path,
        default=PROJECT_DIR / "mcp_servers/commands.json",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--endpoint", default="/mcp")
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--upstream-log", type=Path, required=True)
    return parser


def _write_ready(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _upstream_process_groups() -> list[int]:
    """Return separate process groups created by the stdio MCP transport."""
    if os.name != "posix":
        return []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    rows: list[tuple[int, int, int]] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            rows.append(tuple(map(int, fields)))
        except ValueError:
            continue

    descendants: set[int] = set()
    frontier = {os.getpid()}
    while frontier:
        children = {
            pid for pid, ppid, _ in rows
            if ppid in frontier and pid not in descendants
        }
        descendants.update(children)
        frontier = children

    own_group = os.getpgrp()
    return sorted(
        {
            pgid for pid, _, pgid in rows
            if pid in descendants and pgid > 1 and pgid != own_group
        }
    )


async def run(args: argparse.Namespace) -> None:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
    from fastmcp.server import create_proxy

    from agent_env.http_server import HTTPServerLauncher
    from agent_env.server_catalog import build_server_process, load_server_commands

    commands = load_server_commands(args.commands.resolve())
    if args.server_name not in commands:
        raise ValueError(f"Unknown MCP server: {args.server_name}")
    config = commands[args.server_name]
    process = build_server_process(
        project_dir=PROJECT_DIR,
        server_name=args.server_name,
        config=config,
    )
    command = list(process.command)
    upstream_launcher: HTTPServerLauncher | None = None
    transport: Any
    if config.get("transport") == "http":
        upstream_launcher = HTTPServerLauncher(
            server_name=args.server_name,
            command=command,
            env=process.env,
            cwd=str(process.cwd),
            port=int(config.get("port", 3001)),
            endpoint=str(config.get("endpoint", "/mcp")),
        )
        if not await upstream_launcher.start():
            raise RuntimeError(f"Failed to start upstream {args.server_name!r}")
        transport = StreamableHttpTransport(
            f"http://127.0.0.1:{upstream_launcher.port}{upstream_launcher.endpoint}"
        )
    else:
        args.upstream_log.parent.mkdir(parents=True, exist_ok=True)
        transport = StdioTransport(
            command=command[0],
            args=command[1:],
            env=dict(process.env),
            cwd=str(process.cwd),
            keep_alive=True,
            log_file=args.upstream_log,
        )

    client = Client(
        transport,
        name=f"shared-{args.server_name}",
        timeout=300,
        init_timeout=90,
    )
    try:
        async with client:
            tools = await client.list_tools()
            proxy = create_proxy(client, name=f"Shared {args.server_name}")
            _write_ready(
                args.ready_file,
                {
                    "server_name": args.server_name,
                    "tool_count": len(tools),
                    "endpoint": f"http://{args.host}:{args.port}{args.endpoint}",
                    "upstream_process_groups": _upstream_process_groups(),
                },
            )
            await proxy.run_http_async(
                host=args.host,
                port=args.port,
                path=args.endpoint,
                transport="streamable-http",
                show_banner=False,
                log_level="warning",
                uvicorn_config={"access_log": False},
            )
    finally:
        await transport.close()
        if upstream_launcher is not None:
            await upstream_launcher.stop()


def main() -> int:
    from agent_env.settings import load_project_env

    load_project_env(PROJECT_DIR)
    args = build_arg_parser().parse_args()
    if args.port <= 0 or args.port > 65535:
        print("Error: --port must be between 1 and 65535", file=sys.stderr)
        return 1
    try:
        asyncio.run(run(args))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
