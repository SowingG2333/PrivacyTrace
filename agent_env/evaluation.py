"""Deterministic env trajectory diagnostics; model judges can be layered on later."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from agent_env.tool_outcomes import classify_tool_output, tool_call_signature


def evaluate_trajectory(trajectory: Mapping[str, Any]) -> dict[str, Any]:
    turns = trajectory.get("turns", [])
    assistant_turns = [turn for turn in turns if turn.get("role") == "assistant"]
    executions = [
        call
        for turn in assistant_turns
        for call in turn.get("execution_results", [])
    ]

    outcomes: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    for call in executions:
        kind = call.get("outcome")
        if not kind:
            value = call.get("result", call.get("returned_result"))
            kind = classify_tool_output(value, transport_error=not call.get("success", False)).kind
        outcomes[str(kind)] += 1
        tool = str(call.get("tool", call.get("tool_name", "")))
        params = call.get("parameters", call.get("call_statement", {}))
        signatures[tool_call_signature(tool, params)] += 1

    repeated_calls = sum(count - 1 for count in signatures.values() if count > 1)
    tools_used = Counter(
        str(call.get("tool", call.get("tool_name", ""))) for call in executions
    )
    same_tool_reuses = sum(count - 1 for count in tools_used.values() if count > 1)
    usable = outcomes["success"]
    final_response = assistant_turns[-1].get("content", "").strip() if assistant_turns else ""
    status = trajectory.get("status")
    checks = {
        "conversation_closed_by_user": status == "satisfied",
        "assistant_produced_response": bool(final_response),
        "no_agent_error": status not in {
            "agent_error",
            "environment_error",
            "simulator_error",
        },
        "tool_results_were_usable": not executions or usable > 0,
    }
    token_usage = trajectory.get("token_usage", {})
    agent_tokens = int(
        token_usage.get("agent", {}).get(
            "total_tokens",
            sum(int(turn.get("total_tokens", 0) or 0) for turn in assistant_turns),
        )
        or 0
    )
    simulator_tokens = int(
        token_usage.get("simulator", {}).get("total_tokens", 0) or 0
    )
    return {
        "evaluator": "deterministic",
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": {
            "assistant_turns": len(assistant_turns),
            "tool_calls": len(executions),
            "usable_tool_calls": usable,
            "tool_success_rate": round(usable / len(executions), 4) if executions else None,
            "empty_tool_calls": outcomes["empty"],
            "application_error_calls": outcomes["application_error"],
            "transport_error_calls": outcomes["transport_error"],
            "repeated_tool_calls": repeated_calls,
            "same_tool_reuses": same_tool_reuses,
            "agent_tokens": agent_tokens,
            "simulator_tokens": simulator_tokens,
            "total_known_tokens": agent_tokens + simulator_tokens,
            "total_tokens": agent_tokens,
        },
        "note": "Diagnostics are not a replacement for domain-specific or LLM-judge evaluation.",
    }
