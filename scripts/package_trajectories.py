#!/usr/bin/env python3
"""Package per-scenario trajectory JSON files into the canonical JSONL dataset."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from agent_env.recovery import atomic_write_json  # noqa: E402
from privacy_trace.trajectory_io import index_trajectories  # noqa: E402


DEFAULT_SOURCE_DIR = PROJECT_DIR / "artifacts/trajectories"
DEFAULT_SCENARIOS = PROJECT_DIR / "artifacts/scenario/cases.jsonl"


def read_scenario_index(path: Path) -> dict[str, tuple[str, str]]:
    records: dict[str, tuple[str, str]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not value.get("scenario_id"):
                raise ValueError(f"Invalid scenario record at {path}:{line_number}")
            scenario_id = str(value["scenario_id"])
            if scenario_id in records:
                raise ValueError(f"Duplicate scenario_id in {path}: {scenario_id}")
            records[scenario_id] = (
                str(value.get("profile_id") or ""),
                str(value.get("domain") or ""),
            )
    return records


def package(
    source_dir: Path,
    output_path: Path,
    scenarios_path: Path,
    audit_path: Path,
    summary_path: Path,
) -> dict[str, Any]:
    source_paths = sorted(source_dir.glob("S*.json"))
    if not source_paths:
        raise ValueError(f"No per-scenario trajectories found in {source_dir}")
    expected = read_scenario_index(scenarios_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    domain_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    termination_counts: Counter[str] = Counter()
    agent_models: Counter[str] = Counter()
    simulator_models: Counter[str] = Counter()
    tool_outcomes: Counter[str] = Counter()
    seen_ids: set[str] = set()
    seen_profiles: Counter[str] = Counter()
    errors: list[str] = []
    total_tool_records = 0
    mcp_executed_records = 0
    cache_hit_records = 0
    transport_failure_records = 0
    content_failure_records = 0
    started_at = time.time()
    digest = hashlib.sha256()

    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        try:
            for source_path in source_paths:
                with source_path.open("r", encoding="utf-8") as source_handle:
                    record = json.load(source_handle)
                if not isinstance(record, dict):
                    errors.append(f"{source_path.name}: record is not an object")
                    continue
                scenario_id = str(record.get("scenario_id") or "")
                profile_id = str(record.get("profile_id") or "")
                domain = str(record.get("domain") or "")
                if not scenario_id:
                    errors.append(f"{source_path.name}: missing scenario_id")
                    continue
                if scenario_id in seen_ids:
                    errors.append(f"duplicate scenario_id: {scenario_id}")
                    continue
                seen_ids.add(scenario_id)
                expected_identity = expected.get(scenario_id)
                if expected_identity != (profile_id, domain):
                    errors.append(
                        f"{scenario_id}: expected {expected_identity}, "
                        f"found {(profile_id, domain)}"
                    )
                seen_profiles[profile_id] += 1
                domain_counts[domain] += 1
                status_counts[str(record.get("status", "<missing>"))] += 1
                termination_counts[
                    str(record.get("termination_reason", "<missing>"))
                ] += 1
                run = record.get("run") if isinstance(record.get("run"), dict) else {}
                agent_models[str(run.get("agent_model", record.get("agent_model", "<missing>")))] += 1
                simulator_models[
                    str(run.get("simulator_model", record.get("simulator_model", "<missing>")))
                ] += 1
                tools = record.get("tools_info")
                if not isinstance(tools, list):
                    errors.append(f"{scenario_id}: invalid tools_info")
                    tools = []
                total_tool_records += len(tools)
                for tool in tools:
                    if not isinstance(tool, dict):
                        errors.append(f"{scenario_id}: non-object tool record")
                        continue
                    tool_outcomes[str(tool.get("outcome", "<missing>"))] += 1
                    mcp_executed_records += tool.get("mcp_executed") is True
                    cache_hit_records += tool.get("cache_hit") is True
                    transport_failure_records += tool.get("transport_success") is False
                    content_failure_records += tool.get("content_success") is False

                line = (
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                handle.write(line)
                digest.update(line)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    missing_ids = sorted(set(expected) - seen_ids)
    extra_ids = sorted(seen_ids - set(expected))
    if missing_ids:
        errors.append(f"missing scenario_ids: {missing_ids[:20]}")
    if extra_ids:
        errors.append(f"unexpected scenario_ids: {extra_ids[:20]}")
    unexpected_profile_counts = {
        profile_id: count
        for profile_id, count in seen_profiles.items()
        if count != 4
    }
    if unexpected_profile_counts:
        errors.append(
            "profiles without exactly four trajectories: "
            f"{dict(list(sorted(unexpected_profile_counts.items()))[:20])}"
        )
    if errors:
        temporary.unlink(missing_ok=True)
        raise ValueError("; ".join(errors[:20]))

    os.replace(temporary, output_path)
    verified_refs = index_trajectories(output_path)
    if len(verified_refs) != len(source_paths):
        output_path.unlink(missing_ok=True)
        raise ValueError(
            f"Packaged record count mismatch: {len(verified_refs)} != {len(source_paths)}"
        )

    completed_at = time.time()
    audit = {
        "schema_version": "1.0",
        "path": str(output_path.resolve()),
        "sha256": digest.hexdigest(),
        "record_count": len(verified_refs),
        "expected_count": len(expected),
        "unique_scenario_count": len(seen_ids),
        "unique_profile_count": len(seen_profiles),
        "profiles_with_four_trajectories": sum(
            count == 4 for count in seen_profiles.values()
        ),
        "domain_counts": dict(sorted(domain_counts.items())),
        "status_counts": dict(status_counts.most_common()),
        "termination_reason_counts": dict(termination_counts.most_common()),
        "agent_model_counts": dict(agent_models.most_common()),
        "simulator_model_counts": dict(simulator_models.most_common()),
        "tool_record_count": total_tool_records,
        "mcp_executed_record_count": mcp_executed_records,
        "cache_hit_record_count": cache_hit_records,
        "transport_failure_record_count": transport_failure_records,
        "content_failure_record_count": content_failure_records,
        "tool_outcome_counts": dict(tool_outcomes.most_common()),
        "mapping_error_count": 0,
        "errors": [],
        "passed": True,
    }
    summary = {
        "schema_version": "1.0",
        "run_id": "trajectories",
        "phase": "trajectory",
        "expected_count": len(expected),
        "completed_count": len(verified_refs),
        "failed_count": 0,
        "status": "completed",
        "started_at": started_at,
        "updated_at": completed_at,
        "completed_at": completed_at,
        "dataset_sha256": digest.hexdigest(),
        "audit": audit_path.name,
    }
    atomic_write_json(audit_path, audit)
    atomic_write_json(summary_path, summary)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--audit-output", type=Path)
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    canonical_dir = PROJECT_DIR / "artifacts/trajectories"
    output = (
        args.output or canonical_dir / "trajectories.jsonl"
    ).resolve()
    audit_output = (
        args.audit_output or canonical_dir / "combined_audit.json"
    ).resolve()
    summary_output = (
        args.summary_output or canonical_dir / "_batch_summary.json"
    ).resolve()
    try:
        audit = package(
            source_dir,
            output,
            args.scenarios.resolve(),
            audit_output,
            summary_output,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
