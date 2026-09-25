#!/usr/bin/env python3
"""Create compact offline examples from the canonical synthetic records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DOMAINS = ("travel", "health", "shopping", "career_learning")


def rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_redactions(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


def redact(value: Any, entries: list[dict[str, str]]) -> Any:
    text = json.dumps(value, ensure_ascii=False)
    for entry in entries:
        text = text.replace(entry["find"], entry["replace"])
    return json.loads(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--redactions-file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.source_root.resolve() / "artifacts"
    profiles = list(rows(root / "profile_pool/profiles.jsonl"))
    cases = list(rows(root / "scenario/cases.jsonl"))
    selected_cases = {domain: next(row for row in cases if row["domain"] == domain) for domain in DOMAINS}
    scenario_ids = {row["scenario_id"] for row in selected_cases.values()}
    selected_trajectories = {
        row["scenario_id"]: row
        for row in rows(root / "trajectories/trajectories.jsonl")
        if row["scenario_id"] in scenario_ids
    }
    entries = load_redactions(args.redactions_file)
    args.output.mkdir(parents=True, exist_ok=True)
    for domain, case in selected_cases.items():
        trajectory = selected_trajectories[case["scenario_id"]]
        profile_index = int(case["profile_id"][1:]) - 1
        compact_trajectory = {
            key: trajectory.get(key)
            for key in (
                "schema_version", "scenario_id", "profile_id", "domain", "status",
                "termination_reason", "total_tool_calls", "metrics", "evaluation",
            )
        }
        compact_trajectory["tools_info"] = trajectory.get("tools_info", [])[:2]
        payload = redact(
            {
                "notice": "Synthetic offline example; direct identifiers are not real.",
                "profile_id": case["profile_id"],
                "profile": profiles[profile_index],
                "scenario": case,
                "trajectory_excerpt": compact_trajectory,
            },
            entries,
        )
        (args.output / f"{domain}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(f"wrote {len(DOMAINS)} examples to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

