#!/usr/bin/env python3
"""Finish channel attacks, build fixed-denominator rows, and run the Judge.

This supervisor is intentionally separate from the initial attack process so a
long-running tmux job can be recovered and advanced after the launching Codex
turn ends.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time


PROJECT = Path(__file__).resolve().parent.parent
ATTACK_SESSION = "agent_privacy_channel_attack"
ATTACK_ROOT = PROJECT / "artifacts/privacy_experiments/deepseek_v4_flash_0731/channel_ablation/lossless_tool_catalog"
EVAL_ROOT = PROJECT / "artifacts/privacy_experiments/deepseek_v4_flash_0731/evaluation/channel_ablation"
MODES = (
    "metadata_sequence",
    "metadata_sequence_parameters",
    "metadata_sequence_results",
)


def run(command: list[str], *, env: dict[str, str] | None = None) -> int:
    print("RUN", " ".join(command), flush=True)
    return subprocess.run(command, cwd=PROJECT, env=env, check=False).returncode


def session_exists(name: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def output_counts() -> dict[str, int]:
    return {
        mode: sum(1 for _ in (ATTACK_ROOT / mode).glob("S*.json"))
        for mode in MODES
    }


def attack_command(*, resume: bool, materialize: bool) -> list[str]:
    command = [
        str(PROJECT / ".venv/bin/python"),
        "scripts/run_channel_experiments.py",
        "--input", "artifacts/trajectories/trajectories.jsonl",
        "--output-root", str(ATTACK_ROOT),
        "--modes", *MODES,
        "--workers", "200",
        "--rpm", "600",
        "--observation-format", "lossless_tool_catalog",
        "--temperature", "1.0",
        "--model", "deepseek-v4-flash-0731",
        "--thinking-mode", "disabled",
        "--timeout", "600",
        "--retries", "0",
        "--retry-delay", "0",
        "--summary-every", "20",
        "--run-id", "channel_ablation_v1",
    ]
    if resume:
        command.append("--resume")
    if materialize:
        command.append("--materialize-exhausted")
    return command


def judge_complete() -> bool:
    path = EVAL_ROOT / "judge_first/_batch_summary.json"
    if not path.is_file():
        return False
    value = json.loads(path.read_text(encoding="utf-8"))
    return (
        value.get("fatal_error") in (None, "")
        and int(value.get("failure_count", 1)) == 0
        and value.get("completed_at") is not None
    )


def main() -> int:
    while session_exists(ATTACK_SESSION):
        print("Waiting for initial channel attack session...", flush=True)
        time.sleep(60)

    counts = output_counts()
    print("Initial attack output counts", counts, flush=True)
    for recovery_pass in range(1, 7):
        if all(counts[mode] == 4000 for mode in MODES):
            break
        print(f"Attack recovery pass {recovery_pass}", flush=True)
        run(attack_command(resume=True, materialize=True))
        counts = output_counts()
        print("Attack output counts", counts, flush=True)
    if not all(counts[mode] == 4000 for mode in MODES):
        raise RuntimeError(f"Incomplete attack outputs after recovery: {counts}")

    EVAL_ROOT.mkdir(parents=True, exist_ok=True)
    records = EVAL_ROOT / "attack_records.jsonl"
    build_records = [
        str(PROJECT / ".venv/bin/python"),
        "scripts/build_privacy_eval_records.py",
        "--profiles", "artifacts/profile_pool/profiles.jsonl",
        "--arm", f"metadata_sequence={ATTACK_ROOT / 'metadata_sequence'}",
        "--arm", f"metadata_sequence_parameters={ATTACK_ROOT / 'metadata_sequence_parameters'}",
        "--arm", f"metadata_sequence_results={ATTACK_ROOT / 'metadata_sequence_results'}",
        "--output", str(records),
    ]
    if run(build_records) != 0:
        raise RuntimeError("Failed to build channel evaluation records")

    judge_env = dict(os.environ)
    judge_env["PRIVACY_ATTACK_API_KEY_INDEXES"] = "1,2,3"
    # Allocate the three equivalent Judge credentials in a 5:1:10 ratio.
    # These independent caps sum to the experiment-wide 1,600 RPM limit.
    judge_env["MY_MODEL_API_KEY_RPMS"] = "500,100,1000"
    judge_command = [
        str(PROJECT / ".venv/bin/python"),
        "scripts/run_semantic_judge.py",
        "--records", str(records),
        "--output-root", str(EVAL_ROOT / "judge_first"),
        "--batch-size", "25",
        "--workers", "200",
        "--rpm", "1600",
        "--temperature", "0.0",
        "--model", "glm5-2",
        "--equivalent-glm-pool",
        "--timeout", "600",
        "--retries", "0",
        "--retry-delay", "0",
        "--max-recovery-rounds", "25",
        "--summary-every", "20",
        "--run-id", "channel_judge_first",
    ]
    existing_judge_run = (
        EVAL_ROOT / "judge_first/_recovery/manifest.json"
    ).is_file()
    for judge_pass in range(27):
        if judge_complete():
            break
        command = list(judge_command)
        if judge_pass or existing_judge_run:
            command.extend(["--resume", "--materialize-exhausted"])
        print(f"Judge pass {judge_pass + 1}", flush=True)
        run(command, env=judge_env)
    if not judge_complete():
        raise RuntimeError("Channel Semantic Judge did not complete")

    print("CHANNEL_ABLATION_ATTACK_AND_JUDGE_COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
