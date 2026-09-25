#!/usr/bin/env python3
"""Benchmark real MCP tool calls at bounded session concurrency levels."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

SERVER_ORDER = (
    "Wikipedia",
    "Hugging Face",
    "Reddit",
    "Remoote Jobs",
)

QUERIES: dict[str, tuple[tuple[str, dict[str, Any]], ...]] = {
    "Wikipedia": (
        ("search_wikipedia", {"query": "Bandipur National Park", "limit": 2}),
        ("search_wikipedia", {"query": "Ada Lovelace", "limit": 2}),
        ("search_wikipedia", {"query": "Large language model", "limit": 2}),
        ("search_wikipedia", {"query": "Shanghai", "limit": 2}),
    ),
    "Hugging Face": (
        ("search-datasets", {"query": "climate", "limit": 2}),
        ("search-models", {"query": "translation", "limit": 2}),
        ("search-datasets", {"query": "medicine", "limit": 2}),
        ("search-models", {"query": "robotics", "limit": 2}),
    ),
    "Reddit": (
        ("fetch_reddit_hot_threads", {"subreddit": "MachineLearning", "limit": 2}),
        ("fetch_reddit_hot_threads", {"subreddit": "science", "limit": 2}),
        ("fetch_reddit_hot_threads", {"subreddit": "technology", "limit": 2}),
        ("fetch_reddit_hot_threads", {"subreddit": "Python", "limit": 2}),
    ),
    "Remoote Jobs": (
        ("search_jobs", {"query": "machine learning", "limit": 2}),
        ("search_jobs", {"query": "data engineer", "limit": 2}),
        ("search_jobs", {"query": "python", "limit": 2}),
        ("search_jobs", {"query": "product manager", "limit": 2}),
    ),
}


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def process_tree_snapshot(root_pid: int) -> tuple[int, int]:
    """Return descendant count and aggregate RSS KiB for this benchmark."""
    process = subprocess.Popen(
        ["ps", "-axo", "pid=,ppid=,rss="],
        stdout=subprocess.PIPE,
        text=True,
    )
    output, _ = process.communicate()
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, process.args)
    sampler_pid = process.pid
    rows: dict[int, tuple[int, int]] = {}
    children: dict[int, list[int]] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        pid, ppid, rss = map(int, fields)
        rows[pid] = (ppid, rss)
        children.setdefault(ppid, []).append(pid)
    descendants: set[int] = set()
    frontier = [root_pid]
    while frontier:
        parent = frontier.pop()
        for child in children.get(parent, []):
            if child not in descendants:
                descendants.add(child)
                frontier.append(child)
    descendants.discard(sampler_pid)
    rss_kib = rows.get(root_pid, (0, 0))[1]
    rss_kib += sum(rows.get(pid, (0, 0))[1] for pid in descendants)
    return len(descendants), rss_kib


async def monitor_process_tree(
    root_pid: int,
    stop: asyncio.Event,
) -> dict[str, int]:
    peak_processes = 0
    peak_rss_kib = 0
    while not stop.is_set():
        try:
            process_count, rss_kib = await asyncio.to_thread(
                process_tree_snapshot,
                root_pid,
            )
            peak_processes = max(peak_processes, process_count)
            peak_rss_kib = max(peak_rss_kib, rss_kib)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.2)
        except TimeoutError:
            pass
    return {
        "peak_descendant_processes": peak_processes,
        "peak_rss_kib": peak_rss_kib,
    }


def classify_result(result: Any) -> tuple[str, int, str | None]:
    content = getattr(result, "content", []) or []
    text = "\n".join(
        str(getattr(item, "text", ""))
        for item in content
        if getattr(item, "type", None) == "text"
    ).strip()
    structured = getattr(result, "structured_content", None)
    if not text and structured is not None:
        text = json.dumps(structured, ensure_ascii=False, default=str)
    if bool(getattr(result, "is_error", False)):
        return "application_error", len(text), text[:300]
    lowered = text.lower()
    if lowered.startswith(("error ", "error:", "error retrieving")):
        return "application_error", len(text), text[:300]
    compact = lowered.replace(" ", "")
    if not text or any(
        marker in compact
        for marker in ('"results":[]', '"items":[]', '"jobs":[]')
    ):
        return "empty", len(text), None
    return "success", len(text), None


async def run_one(
    *,
    server_name: str,
    index: int,
    timeout: float,
    commands: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    from agent_env.server_catalog import build_server_process

    tool_name, arguments = QUERIES[server_name][index % len(QUERIES[server_name])]
    process = build_server_process(
        project_dir=PROJECT_DIR,
        server_name=server_name,
        config=commands[server_name],
    )
    transport = None
    started = time.monotonic()
    try:
        with open(os.devnull, "w", encoding="utf-8") as log_handle:
            transport = StdioTransport(
                process.command[0],
                list(process.command[1:]),
                env=dict(process.env),
                cwd=str(process.cwd),
                log_file=log_handle,
            )
            async with Client(transport, timeout=timeout) as client:
                result = await client.call_tool(
                    tool_name,
                    arguments,
                    timeout=timeout,
                    raise_on_error=False,
                )
        outcome, payload_chars, error = classify_result(result)
        return {
            "server": server_name,
            "tool": tool_name,
            "outcome": outcome,
            "seconds": time.monotonic() - started,
            "payload_chars": payload_chars,
            "error": error,
        }
    except Exception as exc:
        return {
            "server": server_name,
            "tool": tool_name,
            "outcome": "transport_error",
            "seconds": time.monotonic() - started,
            "payload_chars": 0,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }


async def run_level(
    *,
    target: str,
    concurrency: int,
    timeout: float,
    commands: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    server_names = (
        [SERVER_ORDER[index % len(SERVER_ORDER)] for index in range(concurrency)]
        if target == "mixed"
        else [target] * concurrency
    )
    stop = asyncio.Event()
    monitor = asyncio.create_task(monitor_process_tree(os.getpid(), stop))
    started = time.monotonic()
    results = await asyncio.gather(
        *(
            run_one(
                server_name=server_name,
                index=index,
                timeout=timeout,
                commands=commands,
            )
            for index, server_name in enumerate(server_names)
        )
    )
    wall_seconds = time.monotonic() - started
    stop.set()
    telemetry = await monitor
    await asyncio.sleep(0.5)
    final_processes, final_rss_kib = await asyncio.to_thread(
        process_tree_snapshot,
        os.getpid(),
    )
    outcomes = Counter(str(item["outcome"]) for item in results)
    latencies = [float(item["seconds"]) for item in results]
    return {
        "target": target,
        "concurrency": concurrency,
        "calls": len(results),
        "outcomes": dict(sorted(outcomes.items())),
        "success_rate": outcomes["success"] / len(results),
        "usable_rate": (
            outcomes["success"] + outcomes["empty"]
        ) / len(results),
        "wall_seconds": wall_seconds,
        "throughput_calls_per_second": len(results) / wall_seconds,
        "latency_seconds": {
            "min": min(latencies),
            "median": statistics.median(latencies),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies),
        },
        "peak_descendant_processes": telemetry["peak_descendant_processes"],
        "peak_rss_mb": telemetry["peak_rss_kib"] / 1024,
        "final_descendant_processes": final_processes,
        "final_rss_mb": final_rss_kib / 1024,
        "errors": [
            {
                "server": item["server"],
                "tool": item["tool"],
                "outcome": item["outcome"],
                "error": item["error"],
            }
            for item in results
            if item["outcome"] in {"application_error", "transport_error"}
        ][:5],
        "by_server": {
            server: dict(
                sorted(
                    Counter(
                        str(item["outcome"])
                        for item in results
                        if item["server"] == server
                    ).items()
                )
            )
            for server in dict.fromkeys(server_names)
        },
    }


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=(*SERVER_ORDER, "mixed"),
        required=True,
    )
    parser.add_argument("--levels", nargs="+", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path)
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    from agent_env.server_catalog import load_server_commands

    commands = load_server_commands(PROJECT_DIR / "mcp_servers/commands.json")
    levels = []
    for concurrency in args.levels:
        if concurrency <= 0:
            raise ValueError("concurrency levels must be positive")
        result = await run_level(
            target=args.target,
            concurrency=concurrency,
            timeout=args.timeout,
            commands=commands,
        )
        levels.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return {
        "schema_version": "1.0",
        "created_at": time.time(),
        "target": args.target,
        "proxy_inherited": any(
            os.environ.get(name)
            for name in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            )
        ),
        "levels": levels,
    }


def main() -> int:
    from agent_env.settings import load_project_env

    load_project_env(PROJECT_DIR)
    args = build_parser().parse_args()
    try:
        report = asyncio.run(async_main(args))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.output:
        atomic_write_json(args.output.resolve(), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
