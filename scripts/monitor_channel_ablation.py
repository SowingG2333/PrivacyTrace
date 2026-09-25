#!/usr/bin/env python3
"""Monitor the channel-ablation Judge every ten minutes and recover its supervisor."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time


PROJECT = Path(__file__).resolve().parent.parent
EVAL_ROOT = PROJECT / "artifacts/privacy_experiments/deepseek_v4_flash_0731/evaluation/channel_ablation"
JUDGE_ROOT = EVAL_ROOT / "judge_first"
LOG_ROOT = EVAL_ROOT / "monitor"
FOLLOWUP_SESSION = "agent_privacy_channel_followup"
INTERVAL_SECONDS = 600


def tmux_alive(name: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def batch_snapshot() -> dict[str, object]:
    saved = 0
    materialized = 0
    slots: Counter[str] = Counter()
    recovery_rounds: Counter[str] = Counter()
    for path in (JUDGE_ROOT / "batches").rglob("*.json"):
        if path.name.startswith("._"):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        saved += 1
        metadata = value.get("metadata") or {}
        if metadata.get("failure_scored_as_incorrect"):
            materialized += 1
        request = metadata.get("request") or {}
        if request.get("key_slot") is not None:
            slots[str(request["key_slot"])] += 1
        if metadata.get("recovery_round") is not None:
            recovery_rounds[str(metadata["recovery_round"])] += 1
    return {
        "saved_batches": saved,
        "materialized_failure_batches": materialized,
        "successful_batches": saved - materialized,
        "key_slots": dict(slots),
        "recovery_rounds": dict(recovery_rounds),
    }


def summary_snapshot() -> dict[str, object]:
    path = JUDGE_ROOT / "_batch_summary.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"summary_parse_error": True}
    fields = (
        "selected_batch_count",
        "runnable_batch_count",
        "finished_batch_count",
        "batch_statuses",
        "failure_count",
        "materialized_failure_count",
        "fatal_error",
        "completed_at",
        "started_at",
        "updated_at",
    )
    return {field: value.get(field) for field in fields}


def restart_followup() -> bool:
    command = (
        f"cd {PROJECT} && set -a && source .env && set +a && "
        "exec ./.venv/bin/python scripts/run_channel_ablation_followup.py"
    )
    return subprocess.run(
        ["tmux", "new-session", "-d", "-s", FOLLOWUP_SESSION, command],
        cwd=PROJECT,
        check=False,
    ).returncode == 0


def write_snapshot(snapshot: dict[str, object]) -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    latest = LOG_ROOT / "latest.json"
    temporary = LOG_ROOT / ".latest.json.tmp"
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, latest)
    with (LOG_ROOT / "progress.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False) + "\n")


def main() -> int:
    while True:
        summary = summary_snapshot()
        batches = batch_snapshot()
        total = int(summary.get("selected_batch_count") or 1124)
        saved = int(batches["saved_batches"])
        complete = bool(summary.get("completed_at")) and saved >= total
        alive = tmux_alive(FOLLOWUP_SESSION)
        restarted = False
        if not complete and not alive:
            restarted = restart_followup()
            alive = tmux_alive(FOLLOWUP_SESSION)
        snapshot = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "interval_seconds": INTERVAL_SECONDS,
            "complete": complete,
            "followup_alive": alive,
            "followup_restarted": restarted,
            "progress_pct": round(100.0 * saved / total, 3) if total else 0.0,
            **summary,
            **batches,
        }
        write_snapshot(snapshot)
        print(json.dumps(snapshot, ensure_ascii=False), flush=True)
        if complete:
            return 0
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
