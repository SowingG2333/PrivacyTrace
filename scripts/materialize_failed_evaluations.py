#!/usr/bin/env python3
"""Materialize exhausted generation failures as fixed-denominator ASR negatives."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES  # noqa: E402
from scripts.run_channel_experiments import (  # noqa: E402
    build_jobs,
    is_usable_output,
    write_json,
)
from scripts.run_privacy_attacks import has_usable_attack  # noqa: E402


def failed_profile(reason: str) -> dict[str, dict[str, Any]]:
    return {
        attribute: {
            "status": "failed",
            "evidence": [],
            "value": None,
            "alternative_values": [],
            "unresolved_reason": reason,
            "confidence": 0.0,
        }
        for attribute in PROFILE_ATTRIBUTES
    }


def channel_failures(
    input_dir: Path,
    output_root: Path,
    model: str,
) -> int:
    from privacy_trace.channel_experiments import GENERATED_MODES

    jobs, _ = build_jobs(input_dir, output_root, set(GENERATED_MODES), None)
    count = 0
    for job in jobs:
        if is_usable_output(job.output_path, job.mode, job.server_name):
            continue
        reason = "Generation failed after the configured retry budget."
        profile = failed_profile(reason)
        write_json(
            job.output_path,
            {
                "profile": profile,
                "inferred_attributes": [],
                "unresolved_attributes": list(PROFILE_ATTRIBUTES),
                "one_shot": {
                    "profile": profile,
                    "inferred_attributes": [],
                    "unresolved_attributes": list(PROFILE_ATTRIBUTES),
                },
                "metadata": {
                    "attack_type": "one_shot",
                    "experiment": "privacy_channel_decomposition",
                    "experiment_mode": job.mode,
                    "model": model,
                    "scenario_id": job.scenario_id,
                    "profile_id": job.profile_id,
                    "domain": job.domain,
                    "server_name": job.server_name,
                    "trajectory_path": str(job.trajectory_path),
                    "failure_scored_as_incorrect": True,
                    "failure_reason": reason,
                },
            },
        )
        count += 1
    return count


def full_failures(input_dir: Path, output_dir: Path, model: str) -> int:
    count = 0
    for input_path in sorted(input_dir.glob("S*.json")):
        output_path = output_dir / input_path.name
        if has_usable_attack(output_path, "one_shot"):
            continue
        trajectory = json.loads(input_path.read_text(encoding="utf-8"))
        reason = "Generation failed after the configured retry budget."
        write_json(
            output_path,
            {
                "profile": failed_profile(reason),
                "metadata": {
                    "attack_type": "one_shot",
                    "model": model,
                    "scenario_id": trajectory.get("scenario_id") or input_path.stem,
                    "profile_id": trajectory.get("profile_id"),
                    "domain": trajectory.get("domain"),
                    "trajectory_path": str(input_path),
                    "failure_scored_as_incorrect": True,
                    "failure_reason": reason,
                },
            },
        )
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("channel", "full"), required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.kind == "channel":
        count = channel_failures(input_dir, output_dir, args.model)
    else:
        count = full_failures(input_dir, output_dir, args.model)
    print(f"Materialized {count} exhausted {args.kind} failures as ASR negatives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
