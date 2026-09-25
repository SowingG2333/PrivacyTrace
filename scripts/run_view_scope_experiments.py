#!/usr/bin/env python3
"""Run profile-level cross-domain all-server privacy attacks."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from agent_env.recovery import (  # noqa: E402
    FailureLedger, RecoveryContract, RollingRateLimiter, atomic_write_json,
    ensure_recovery_manifest, file_hashes, is_fatal_provider_error,
    is_nonretryable_request_error,
    is_transient_provider_error, portable_path, require_nonempty_model,
)
from privacy_trace.attacker_view import (  # noqa: E402
    ATTACKER_OBSERVATION_FORMATS, attacker_view_policy_for_format,
    prepare_attacker_view_for_format,
)
from privacy_trace.llm_client import DirectOpenAIClient  # noqa: E402
from privacy_trace.privacy_attacker import (  # noqa: E402
    ONE_SHOT_PROMPT_TEMPLATE, PROFILE_ATTRIBUTES, parse_json_output,
    validate_one_shot_output,
)
from privacy_trace.trajectory_io import index_trajectories, trajectory_input_files  # noqa: E402

DOMAIN_ORDER = ("health", "shopping", "career_learning", "travel")


def cross_domain_observation(segments: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Render a global catalog while retaining domain-local event provenance."""
    catalog: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    events: list[dict[str, Any]] = []
    raw_count = visible_count = 0
    for segment in segments:
        view = prepare_attacker_view_for_format(
            segment["tools_info"], segment["observation_format"]
        )
        raw_count += view.stats.raw_call_count
        visible_count += view.stats.visible_call_count
        for local_index, call in enumerate(view.calls, start=1):
            identity = (
                str(call.get("server_name") or call.get("server") or "unknown"),
                str(call.get("tool_name") or call.get("tool") or "unknown"),
            )
            if identity not in catalog:
                catalog[identity] = (f"T{len(catalog) + 1}", call)
            events.append({
                "domain": segment["domain"], "scenario_id": segment["scenario_id"],
                "local_index": local_index, "call": call, "reference": catalog[identity][0],
            })
    def packed(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    lines = ["## Global tool catalog"]
    for (server, tool), (reference, call) in catalog.items():
        lines.extend([f"--- {reference} ---", f"Server: {server}", f"Tool Name: {tool}"])
        if call.get("tool_description"):
            lines.append(f"Tool Description: {call['tool_description']}")
        if call.get("tool_schema") not in (None, {}, ""):
            lines.append(f"Tool Schema: {packed(call['tool_schema'])}")
    lines.append("\n## Domain-segmented tool-call events")
    for event in events:
        call = event["call"]
        lines.extend([
            f"--- {event['domain']} / {event['scenario_id']} / event {event['local_index']} ---",
            f"Domain: {event['domain']}", f"Scenario: {event['scenario_id']}",
            f"Tool Reference: {event['reference']}",
        ])
        statement = call.get("call_statement", call.get("parameters"))
        if statement is not None:
            lines.append(f"Tool Call Statement: {packed(statement)}")
        result = call.get("returned_result", call.get("result"))
        if result in (None, "") and call.get("error") not in (None, ""):
            result = {"error": call["error"]}
        if result is not None:
            lines.append(f"Tool Returned Result: {packed(result)}")
    if not events:
        lines.append("No observable tool calls.")
    text = "\n".join(lines)
    return text, {
        "raw_call_count": raw_count,
        "visible_call_count": visible_count,
        "prior_equivalent": visible_count == 0,
        "tool_catalog_count": len(catalog),
        "observation_chars": len(text),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--input", type=Path, required=True)
    value.add_argument("--output-root", type=Path, required=True)
    value.add_argument(
        "--limit",
        type=int,
        help="Limit input trajectories; must leave complete four-domain profiles.",
    )
    value.add_argument("--scope", choices=["cross_domain_all_servers"], required=True)
    value.add_argument("--observation-format", choices=ATTACKER_OBSERVATION_FORMATS, default="lossless_tool_catalog")
    value.add_argument("--model", required=True)
    value.add_argument("--temperature", type=float, default=1.0)
    value.add_argument("--thinking-mode", choices=["disabled", "enabled"], default="disabled")
    value.add_argument("--workers", type=int, default=100)
    value.add_argument("--rpm", type=int, default=500)
    value.add_argument("--timeout", type=float, default=600.0)
    value.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Compatibility option. None is required; service output default is used.",
    )
    value.add_argument("--retries", type=int, default=5)
    value.add_argument("--retry-delay", type=float, default=2.0)
    value.add_argument("--max-recovery-rounds", type=int, default=5)
    value.add_argument("--materialize-exhausted", action="store_true")
    value.add_argument("--resume", action="store_true")
    return value


def main() -> int:
    from agent_env.settings import load_project_env
    load_project_env(PROJECT_DIR)
    args = parser().parse_args()
    if args.max_tokens is not None:
        raise SystemExit(
            "Privacy Attack V1 uses the service-default output limit; omit --max-tokens."
        )
    require_nonempty_model(args.model, stage="cross-domain privacy attack")
    if (
        args.workers <= 0
        or args.rpm <= 0
        or args.retries < 0
        or args.limit is not None and args.limit <= 0
    ):
        raise SystemExit("invalid worker, rpm, or retry setting")
    key_pool_size = DirectOpenAIClient.environment_key_count()
    if key_pool_size <= 0:
        raise SystemExit("no enabled MY_MODEL_API_KEY or MY_MODEL_API_KEYS slot")
    refs = index_trajectories(args.input.resolve(), args.limit)
    grouped: dict[str, list[Any]] = defaultdict(list)
    for ref in refs:
        grouped[str(ref.read().get("profile_id") or "")].append(ref)
    profiles: dict[str, list[Any]] = {}
    for profile_id, items in grouped.items():
        domains = [str(item.read().get("domain") or "") for item in items]
        if len(items) != 4 or set(domains) != set(DOMAIN_ORDER):
            raise SystemExit(f"{profile_id}: expected exactly one trajectory for every domain")
        profiles[profile_id] = sorted(items, key=lambda item: DOMAIN_ORDER.index(str(item.read()["domain"])))
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    recovery_root = root / "_recovery"
    contract = RecoveryContract(
        run_id=root.name, phase="cross_domain_all_servers", model=args.model,
        prompt_version=f"cross_domain_global_catalog_{args.observation_format}", seed=None,
        input_hashes=file_hashes(
            trajectory_input_files(args.input.resolve()),
            relative_to=PROJECT_DIR,
        ),
        config={"scope": args.scope, "limit": args.limit, "temperature": args.temperature, "thinking_mode": args.thinking_mode,
                "timeout": args.timeout, "rpm": args.rpm, "max_tokens": None,
                "output_limit_source": "service_default", "domain_order": list(DOMAIN_ORDER),
                "direct_transport": "trust_env_false",
                "api_key_pool_size": key_pool_size,
                "attacker_observation_format": args.observation_format}, required_model=None,
    )
    ensure_recovery_manifest(recovery_root / "manifest.json", contract, workers=args.workers, resume=args.resume)
    ledger = FailureLedger(recovery_root, phase="cross_domain_all_servers", run_id=root.name)
    client = DirectOpenAIClient.from_environment(timeout=args.timeout, max_connections=min(args.workers, 200))
    limiter = RollingRateLimiter(args.rpm)
    manifest = {"model": args.model, "profile_count": len(profiles), "limit": args.limit, "domain_order": list(DOMAIN_ORDER),
                "max_tokens": None, "output_limit_source": "service_default",
                "direct_transport": "trust_env_false", "api_key_pool_size": key_pool_size}
    atomic_write_json(root / "_manifest.json", manifest)

    def execute(profile_id: str, items: list[Any]) -> dict[str, Any]:
        output = root / f"{profile_id}.json"
        if args.resume and output.is_file():
            return {"profile_id": profile_id, "status": "skipped"}
        recovery_round = ledger.recovery_round(profile_id, resume=args.resume)
        def materialize_failure(reason: str) -> dict[str, Any]:
            profile = {
                attribute: {
                    "status": "failed", "value": None, "evidence": [],
                    "alternative_values": [], "unresolved_reason": reason,
                }
                for attribute in PROFILE_ATTRIBUTES
            }
            atomic_write_json(
                output,
                {
                    "profile": profile,
                    "metadata": {
                        "attack_type": "one_shot", "scope": args.scope,
                        "profile_id": profile_id, "model": args.model,
                        "domains": list(DOMAIN_ORDER),
                        "failure_scored_as_incorrect": True,
                        "failure_reason": reason,
                        "recovery_round": recovery_round,
                    },
                },
            )
            return {"profile_id": profile_id, "status": "materialized_failure"}
        rows = [item.read() for item in items]
        if recovery_round > args.max_recovery_rounds:
            if not args.materialize_exhausted:
                return {"profile_id": profile_id, "status": "failed", "error": "recovery exhausted"}
            return materialize_failure("recovery exhausted")
        text, stats = cross_domain_observation([{"domain": row["domain"], "scenario_id": row["scenario_id"], "tools_info": row["tools_info"], "observation_format": args.observation_format} for row in rows])
        prompt = ONE_SHOT_PROMPT_TEMPLATE.replace("${tools_info}", text).replace("${profile_attributes}", json.dumps(PROFILE_ATTRIBUTES, ensure_ascii=False))
        error = "unknown"
        for attempt in range(1, args.retries + 2):
            try:
                response = client.complete_json(prompt=prompt, model=args.model, temperature=args.temperature, stage="cross_domain", limiter=limiter, thinking_mode=args.thinking_mode)
                if response.metadata.get("finish_reason") == "length":
                    raise RuntimeError("response truncated: finish_reason=length")
                prediction = validate_one_shot_output(parse_json_output(response.text))
                atomic_write_json(output, {"profile": prediction["profile"], "one_shot": prediction, "metadata": {
                    "attack_type": "one_shot", "scope": args.scope, "profile_id": profile_id, "model": args.model,
                    "temperature": args.temperature, "domains": list(DOMAIN_ORDER),
                    "attacker_view_policy": attacker_view_policy_for_format(args.observation_format),
                    "attacker_observation_format": args.observation_format,
                    "attempt": attempt,
                    **stats,
                    "request": response.metadata,
                }})
                return {"profile_id": profile_id, "status": "completed"}
            except Exception as exc:
                error = str(exc)
                if is_fatal_provider_error(exc):
                    break
                if is_nonretryable_request_error(exc):
                    if args.materialize_exhausted:
                        return materialize_failure(error)
                    break
                if attempt <= args.retries:
                    time.sleep(min(30.0, args.retry_delay * 2 ** (attempt - 1)) * random.uniform(.75, 1.25))
        return {"profile_id": profile_id, "status": "failed", "error": error, "recovery_round": recovery_round}

    results: list[dict[str, Any]] = []
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(execute, profile_id, items) for profile_id, items in profiles.items()]
            for future in as_completed(futures):
                result = future.result(); results.append(result)
                if result["status"] in {"completed", "skipped", "materialized_failure"}:
                    ledger.resolve(result["profile_id"])
                else:
                    ledger.record_failure(result["profile_id"], error_type="cross_domain", error=result.get("error", "failed"), attempts=args.retries + 1, recovery_round=result.get("recovery_round", 0))
    finally:
        client.close()
    atomic_write_json(root / "_batch_summary.json", {"selected_count": len(profiles), "statuses": {s: sum(x["status"] == s for x in results) for s in {x["status"] for x in results}}, "results": sorted(results, key=lambda x: x["profile_id"])})
    return 0 if not ledger.unresolved() else 1


if __name__ == "__main__":
    raise SystemExit(main())
