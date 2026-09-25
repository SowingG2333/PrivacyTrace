#!/usr/bin/env python3
"""Offline integrity check for the anonymous review repository."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOMAINS = {"travel", "health", "shopping", "career_learning"}


def main() -> int:
    examples = {}
    for domain in DOMAINS:
        path = ROOT / "examples" / f"{domain}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        assert value["scenario"]["domain"] == domain
        assert value["scenario"]["scenario_id"] == value["trajectory_excerpt"]["scenario_id"]
        assert len(value["profile"]) == 17
        examples[domain] = value["scenario"]["scenario_id"]

    commands = json.loads((ROOT / "mcp_servers/commands.json").read_text(encoding="utf-8"))
    mcp_manifest = json.loads((ROOT / "mcp_servers/manifest.json").read_text(encoding="utf-8"))
    assert len(commands) == 25
    assert set(commands) == set(mcp_manifest["servers"])

    results = json.loads((ROOT / "results/paper/data/experimental_summary.json").read_text(encoding="utf-8"))
    dataset = results["dataset"]
    assert dataset["profiles"] == 1000
    assert dataset["scenarios"] == 4000
    assert dataset["trajectories"] == 4000
    assert dataset["tool_records"] == 67950

    print("examples:", ", ".join(f"{key}={examples[key]}" for key in sorted(examples)))
    print("MCP catalog: 25 servers")
    print("frozen results: 1000 profiles, 4000 trajectories, 67950 tool records")
    print("offline release smoke test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

