#!/usr/bin/env python3
"""Stream descriptive statistics over trajectory JSON artifacts."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from privacy_trace.trajectory_io import index_trajectories  # noqa: E402


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def numeric_summary(values: Iterable[int | float]) -> dict[str, Any]:
    numbers = [float(value) for value in values]
    if not numbers:
        return {"count": 0}
    return {
        "count": len(numbers),
        "min": min(numbers),
        "p25": percentile(numbers, 0.25),
        "median": percentile(numbers, 0.5),
        "p75": percentile(numbers, 0.75),
        "p90": percentile(numbers, 0.9),
        "p95": percentile(numbers, 0.95),
        "p99": percentile(numbers, 0.99),
        "max": max(numbers),
        "mean": statistics.fmean(numbers),
        "sum": sum(numbers),
    }


def counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {
        str(key): value
        for key, value in sorted(
            counter.items(), key=lambda item: (-item[1], str(item[0]))
        )
    }


def outcome_summary(counter: Counter[Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, Counter[str]] = {}
    for raw_key, count in counter.items():
        label, outcome = str(raw_key).rsplit("|", 1)
        grouped.setdefault(label, Counter())[outcome] += count
    result: dict[str, dict[str, Any]] = {}
    for label, outcomes in sorted(
        grouped.items(),
        key=lambda item: (-sum(item[1].values()), item[0]),
    ):
        total = sum(outcomes.values())
        usable = outcomes.get("success", 0)
        result[label] = {
            "total": total,
            "usable": usable,
            "unusable": total - usable,
            "usable_rate": usable / total if total else None,
            "outcomes": counter_dict(outcomes),
        }
    return result


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


def classify_agent_error(error: str) -> str:
    normalized = error.lower()
    if "exceeded max retries count" in normalized:
        return "tool_retry_limit"
    if "connection error" in normalized:
        return "model_connection_error"
    if "not a multimodal model" in normalized:
        return "model_multimodal_unsupported"
    if "context length" in normalized or "context window" in normalized:
        return "model_context_length"
    if "rate limit" in normalized or "status_code: 429" in normalized:
        return "model_rate_limit"
    if "status_code: 400" in normalized:
        return "model_http_400_other"
    return "other"


def review(input_path: Path) -> dict[str, Any]:
    paths = index_trajectories(input_path)
    counters: dict[str, Counter[Any]] = {
        name: Counter()
        for name in (
            "domain",
            "status",
            "termination_reason",
            "evaluation_passed",
            "server_selection",
            "agent_model",
            "simulator_model",
            "provider_model",
            "proxy_enabled",
            "key_rpms",
            "max_in_flight",
            "agent_max_total_tokens",
            "tool_outcome",
            "tool_transport_success",
            "tool_content_success",
            "tool_server",
            "tool_outcome_by_server",
            "tool_outcome_by_qualified_tool",
            "status_by_domain",
            "evaluation_by_domain",
            "agent_error_class",
            "agent_error_category",
            "agent_error_category_by_domain",
            "agent_error_category_by_scenario_bucket",
        )
    }
    metrics: dict[str, list[int | float]] = {
        name: []
        for name in (
            "file_bytes",
            "duration_seconds",
            "turns",
            "user_turns",
            "assistant_turns",
            "total_tool_calls",
            "configured_servers",
            "connected_servers",
            "selected_tools",
            "agent_tokens",
            "simulator_tokens",
            "known_total_tokens",
            "simulator_format_failures",
        )
    }
    structural = Counter()
    seen_ids: set[str] = set()
    trajectories_with = Counter()
    total_tool_records = 0
    agent_error_samples: dict[str, list[dict[str, str]]] = {}
    tool_error_samples: dict[str, list[dict[str, Any]]] = {}

    for path in paths:
        metrics["file_bytes"].append(path.byte_length)
        try:
            record = path.read()
        except (OSError, json.JSONDecodeError):
            structural["parse_errors"] += 1
            continue
        if not isinstance(record, dict):
            structural["non_object_records"] += 1
            continue

        scenario_id = record.get("scenario_id")
        if not scenario_id:
            structural["missing_scenario_id"] += 1
        elif str(scenario_id) in seen_ids:
            structural["duplicate_scenario_id"] += 1
        else:
            seen_ids.add(str(scenario_id))
        for field in ("profile_id", "domain", "status", "turns"):
            if field not in record:
                structural[f"missing_{field}"] += 1

        counters["domain"][record.get("domain", "<missing>")] += 1
        counters["status"][record.get("status", "<missing>")] += 1
        counters["status_by_domain"][
            f"{record.get('domain', '<missing>')}|{record.get('status', '<missing>')}"
        ] += 1
        counters["termination_reason"][
            record.get("termination_reason", "<missing>")
        ] += 1
        evaluation = record.get("evaluation")
        evaluation_passed = (
            evaluation.get("passed")
            if isinstance(evaluation, dict)
            else "<missing>"
        )
        counters["evaluation_passed"][evaluation_passed] += 1
        counters["evaluation_by_domain"][
            f"{record.get('domain', '<missing>')}|{evaluation_passed}"
        ] += 1
        counters["server_selection"][
            record.get("server_selection", "<missing>")
        ] += 1

        started_at = record.get("started_at")
        completed_at = record.get("completed_at")
        if isinstance(started_at, (int, float)) and isinstance(
            completed_at, (int, float)
        ):
            metrics["duration_seconds"].append(max(0, completed_at - started_at))

        turns = record.get("turns") if isinstance(record.get("turns"), list) else []
        user_turns = sum(turn.get("role") == "user" for turn in turns if isinstance(turn, dict))
        assistant_turns = sum(
            turn.get("role") == "assistant" for turn in turns if isinstance(turn, dict)
        )
        metrics["turns"].append(len(turns))
        metrics["user_turns"].append(user_turns)
        metrics["assistant_turns"].append(assistant_turns)
        if any(
            isinstance(turn, dict)
            and turn.get("role") == "assistant"
            and not str(turn.get("content") or "").strip()
            for turn in turns
        ):
            trajectories_with["empty_agent_response"] += 1
        if record.get("termination_reason") == "agent_runtime_error":
            errors = [
                str(turn.get("error"))
                for turn in turns
                if isinstance(turn, dict)
                and turn.get("role") == "assistant"
                and turn.get("error")
            ]
            error = errors[-1] if errors else "<missing>"
            error_class = error.split(":", 1)[0]
            category = classify_agent_error(error)
            counters["agent_error_class"][error_class] += 1
            counters["agent_error_category"][category] += 1
            counters["agent_error_category_by_domain"][
                f"{record.get('domain', '<missing>')}|{category}"
            ] += 1
            try:
                scenario_number = int(str(scenario_id).removeprefix("S"))
                bucket_start = ((scenario_number - 1) // 500) * 500 + 1
                bucket = f"S{bucket_start:04d}-S{bucket_start + 499:04d}"
            except (TypeError, ValueError):
                bucket = "<unknown>"
            counters["agent_error_category_by_scenario_bucket"][
                f"{bucket}|{category}"
            ] += 1
            samples = agent_error_samples.setdefault(category, [])
            if len(samples) < 3:
                samples.append(
                    {
                        "scenario_id": str(scenario_id),
                        "domain": str(record.get("domain", "<missing>")),
                        "error": error[:1000],
                    }
                )

        tools = record.get("tools_info")
        if not isinstance(tools, list):
            structural["missing_or_invalid_tools_info"] += 1
            tools = []
        total_tool_calls = int(record.get("total_tool_calls", len(tools)) or 0)
        metrics["total_tool_calls"].append(total_tool_calls)
        if total_tool_calls == 0:
            trajectories_with["zero_tool_calls"] += 1
        if any(
            isinstance(tool, dict) and tool.get("success") is not True
            for tool in tools
        ):
            trajectories_with["tool_failure"] += 1
        for tool in tools:
            if not isinstance(tool, dict):
                structural["invalid_tool_record"] += 1
                continue
            total_tool_records += 1
            counters["tool_outcome"][tool.get("outcome", "<missing>")] += 1
            server_name = tool.get("server", "<missing>")
            tool_name = tool.get("tool", tool.get("tool_name", "<missing>"))
            outcome = tool.get("outcome", "<missing>")
            counters["tool_outcome_by_server"][
                f"{server_name}|{outcome}"
            ] += 1
            counters["tool_outcome_by_qualified_tool"][
                f"{server_name}:{tool_name}|{outcome}"
            ] += 1
            if outcome != "success":
                qualified_tool = f"{server_name}:{tool_name}"
                samples = tool_error_samples.setdefault(qualified_tool, [])
                if len(samples) < 3:
                    samples.append(
                        {
                            "scenario_id": str(scenario_id),
                            "outcome": str(outcome),
                            "parameters": tool.get("parameters", {}),
                            "error": str(tool.get("error", ""))[:2000],
                        }
                    )
            counters["tool_transport_success"][
                tool.get("transport_success", "<missing>")
            ] += 1
            counters["tool_content_success"][
                tool.get("content_success", "<missing>")
            ] += 1
            counters["tool_server"][server_name] += 1

        connections = record.get("server_connections")
        if not isinstance(connections, dict):
            structural["missing_server_connections"] += 1
            connections = {}
        configured = int(connections.get("configured_count", 0) or 0)
        connected = int(connections.get("connected_count", 0) or 0)
        metrics["configured_servers"].append(configured)
        metrics["connected_servers"].append(connected)
        if connected != configured or connections.get("failed_servers"):
            trajectories_with["server_connection_degraded"] += 1

        selected_tools = (
            record.get("task", {}).get("metadata", {}).get("selected_tools", [])
            if isinstance(record.get("task"), dict)
            else []
        )
        metrics["selected_tools"].append(
            len(selected_tools) if isinstance(selected_tools, list) else 0
        )

        usage = record.get("token_usage")
        usage = usage if isinstance(usage, dict) else {}
        agent_usage = usage.get("agent") if isinstance(usage.get("agent"), dict) else {}
        simulator_usage = (
            usage.get("simulator")
            if isinstance(usage.get("simulator"), dict)
            else {}
        )
        metrics["agent_tokens"].append(int(agent_usage.get("total_tokens", 0) or 0))
        metrics["simulator_tokens"].append(
            int(simulator_usage.get("total_tokens", 0) or 0)
        )
        metrics["known_total_tokens"].append(
            int(usage.get("known_total_tokens", record.get("total_known_tokens", 0)) or 0)
        )
        metrics["simulator_format_failures"].append(
            sum(
                len(turn.get("simulator_format_failures", []))
                for turn in turns
                if isinstance(turn, dict)
                and isinstance(turn.get("simulator_format_failures", []), list)
            )
        )

        run = record.get("run") if isinstance(record.get("run"), dict) else {}
        direct_api = (
            run.get("direct_api") if isinstance(run.get("direct_api"), dict) else {}
        )
        counters["agent_model"][run.get("agent_model", "<missing>")] += 1
        counters["simulator_model"][run.get("simulator_model", "<missing>")] += 1
        counters["provider_model"][direct_api.get("provider_model", "<missing>")] += 1
        counters["proxy_enabled"][direct_api.get("proxy_enabled", "<missing>")] += 1
        key_rpms = direct_api.get("key_rpms", "<missing>")
        counters["key_rpms"][
            ",".join(str(value) for value in key_rpms)
            if isinstance(key_rpms, list)
            else key_rpms
        ] += 1
        counters["max_in_flight"][direct_api.get("max_in_flight", "<missing>")] += 1
        limits = record.get("environment", {}).get("limits", {})
        agent_limits = limits.get("agent", {}) if isinstance(limits, dict) else {}
        max_total_tokens = (
            agent_limits.get("max_total_tokens", "<missing>")
            if isinstance(agent_limits, dict)
            else "<missing>"
        )
        counters["agent_max_total_tokens"][
            "null" if max_total_tokens is None else max_total_tokens
        ] += 1

    return {
        "schema_version": "1.0",
        "input": str(input_path.resolve()),
        "trajectory_file_count": len(paths),
        "parsed_record_count": len(paths) - structural["parse_errors"],
        "unique_scenario_count": len(seen_ids),
        "structural_diagnostics": counter_dict(structural),
        "distributions": {
            name: counter_dict(counter) for name, counter in counters.items()
        },
        "numeric_summaries": {
            name: numeric_summary(values) for name, values in metrics.items()
        },
        "tool_record_count": total_tool_records,
        "tool_quality_by_server": outcome_summary(
            counters["tool_outcome_by_server"]
        ),
        "tool_quality_by_qualified_tool": outcome_summary(
            counters["tool_outcome_by_qualified_tool"]
        ),
        "trajectory_diagnostics": counter_dict(trajectories_with),
        "agent_error_samples": agent_error_samples,
        "tool_error_samples": tool_error_samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = review(args.input.resolve())
    if args.output:
        atomic_write_json(args.output.resolve(), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
