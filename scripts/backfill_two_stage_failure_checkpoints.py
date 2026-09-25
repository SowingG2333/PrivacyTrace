#!/usr/bin/env python3
"""Backfill audit-only Stage 1 checkpoints for existing failed two-stage runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from agent_env.recovery import atomic_write_json  # noqa: E402
from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES  # noqa: E402


def failed_stage1(final_metadata: dict[str, Any]) -> dict[str, Any]:
    reason = str(final_metadata.get("failure_reason") or "failed before Stage 1")
    return {
        "stage1": {
            "profile": {
                attribute: {
                    "status": "unresolved",
                    "evidence": [],
                    "value": None,
                    "confidence": None,
                    "alternative_values": [],
                    "unresolved_reason": "Stage 1 was not generated: " + reason,
                }
                for attribute in PROFILE_ATTRIBUTES
            },
            "inferred_attributes": [],
            "unresolved_attributes": list(PROFILE_ATTRIBUTES),
        },
        "metadata": {
            "model": final_metadata.get("model"),
            "trajectory_sha256": final_metadata.get("trajectory_sha256"),
            "scenario_id": final_metadata.get("scenario_id"),
            "profile_id": final_metadata.get("profile_id"),
            "domain": final_metadata.get("domain"),
            "attacker_view_policy": final_metadata.get("attacker_view_policy"),
            "attacker_observation_format": final_metadata.get(
                "attacker_observation_format"
            ),
            "failure_scored_as_incorrect": True,
            "stage1_generation_failed": True,
            "failure_reason": reason,
            "recovery_round": final_metadata.get("recovery_round"),
            "attempt": final_metadata.get("attempt"),
            "request": {
                "stage": "stage1",
                "model": final_metadata.get("model"),
                "max_tokens": None,
                "output_limit_source": "service_default",
                "direct_transport": "trust_env_false",
                "error": reason,
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    stage1_dir = output_dir / "_stage1"
    stage1_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for final_path in sorted(output_dir.glob("S*.json")):
        checkpoint_path = stage1_dir / final_path.name
        if checkpoint_path.exists():
            continue
        final = json.loads(final_path.read_text(encoding="utf-8"))
        metadata = final.get("metadata")
        if not isinstance(metadata, dict) or not metadata.get("failure_scored_as_incorrect"):
            raise RuntimeError(
                f"Missing Stage 1 checkpoint is not a materialized failure: {final_path}"
            )
        atomic_write_json(checkpoint_path, failed_stage1(metadata))
        created += 1
    print(json.dumps({"created": created, "stage1_checkpoints": len(list(stage1_dir.glob('S*.json')))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
