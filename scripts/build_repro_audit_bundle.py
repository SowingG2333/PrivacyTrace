#!/usr/bin/env python3
"""Build a read-only-derived reproducibility audit bundle from frozen artifacts."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from typing import Any, Iterable

from privacy_trace.attacker_view import prepare_attacker_view_for_format
from privacy_trace.channel_experiments import (
    ATTRIBUTE_DESCRIPTIONS,
    SERVER_VIEW,
    build_observation_prompt_details,
)
from privacy_trace.hybrid_judge import ATTRIBUTE_RUBRICS, semantic_correct
from privacy_trace.privacy_attacker import ONE_SHOT_PROMPT_TEMPLATE, PROFILE_ATTRIBUTES
from privacy_trace.profile_generator import (
    COUNTRY_PROFILES,
    FULL_SCHEMA_FIELDS,
    REAL_TARGETS_PATH,
    build_government_id_audit_prompt,
)
from scripts.run_view_scope_experiments import DOMAIN_ORDER, cross_domain_observation


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/repro_audit"
EXPERIMENT = ROOT / "artifacts/privacy_experiments/deepseek_v4_flash_0731"
RECORDS = EXPERIMENT / "evaluation/attack_records.jsonl"
JUDGE = EXPERIMENT / "evaluation/judge_first"
CATALOG = EXPERIMENT / "full_trajectory/lossless_tool_catalog/one_shot"
TRAJECTORIES = ROOT / "artifacts/trajectories/trajectories.jsonl"
PROFILES = ROOT / "artifacts/profile_pool/profiles.jsonl"


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    values = list(rows)
    if not values:
        raise ValueError(f"No rows for {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values[0]))
        writer.writeheader()
        writer.writerows(values)


def load_judgments(root: Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "batches").rglob("*.json")):
        if path.name.startswith("._"):
            continue
        for item in json.loads(path.read_text(encoding="utf-8")).get("judgments", []):
            output[str(item["case_id"])] = item
    return output


def old_ascii_normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def posthoc_correct(record: dict[str, Any], frozen_correct: bool) -> bool:
    false_positive = (
        frozen_correct
        and record.get("attribute") == "name"
        and record.get("strict") is True
        and str(record.get("truth")) != str(record.get("prediction"))
        and not old_ascii_normalize(record.get("truth"))
        and not old_ascii_normalize(record.get("prediction"))
    )
    return bool(frozen_correct and not false_positive)


def call_bin(value: int | None) -> str:
    if value is None:
        return "missing"
    if value == 0:
        return "0"
    if value <= 5:
        return "1-5"
    if value <= 10:
        return "6-10"
    if value <= 20:
        return "11-20"
    if value <= 40:
        return "21-40"
    return "41+"


def build_call_count_files() -> dict[str, Any]:
    judgments = load_judgments(JUDGE)
    rows = [row for row in iter_jsonl(RECORDS) if row.get("arm") == "catalog_one_shot"]
    by_view: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_view[str(row["view_id"])].append(row)
    trajectory_stats = {}
    for trajectory in iter_jsonl(TRAJECTORIES):
        recomputed = prepare_attacker_view_for_format(
            trajectory.get("tools_info", []), "lossless_tool_catalog"
        )
        names = []
        for call in recomputed.calls:
            name = str(call.get("server_name") or call.get("server") or "unknown")
            if name not in names:
                names.append(name)
        trajectory_stats[str(trajectory["scenario_id"])] = (recomputed.stats, names)
    details = []
    bin_summary: dict[str, Counter[str]] = defaultdict(Counter)
    zero_kinds: Counter[str] = Counter()
    for view_id, items in sorted(by_view.items()):
        first = items[0]
        output = json.loads((CATALOG / f"{view_id}.json").read_text(encoding="utf-8"))
        metadata = output.get("metadata", {})
        recorded = metadata.get("visible_call_count")
        recorded_count = int(recorded) if recorded is not None else None
        recomputed_stats, servers = trajectory_stats[view_id]
        frozen_correct = 0
        corrected = 0
        for row in items:
            is_correct = bool(semantic_correct(row, judgments)[0])
            frozen_correct += int(is_correct)
            corrected += int(posthoc_correct(row, is_correct))
        failed = bool(first.get("generation_failed"))
        label = call_bin(recorded_count)
        if recorded_count == 0:
            if metadata.get("raw_zero_call_prior_baseline"):
                zero_kinds["raw_zero_call"] += 1
            elif metadata.get("filtered_to_prior_equivalent"):
                zero_kinds["filtered_to_zero"] += 1
            else:
                zero_kinds["zero_unspecified"] += 1
        details.append(
            {
                "view_id": view_id,
                "profile_id": first["profile_id"],
                "domain": first["domain"],
                "server": " | ".join(servers),
                "visible_call_count": "" if recorded_count is None else recorded_count,
                "visible_call_count_recomputed": recomputed_stats.visible_call_count,
                "raw_call_count": metadata.get("raw_call_count", ""),
                "generation_failed": failed,
                "failure_reason": first.get("failure_reason") or metadata.get("failure_reason") or "",
                "correct_slots_frozen": frozen_correct,
                "correct_slots_posthoc": corrected,
                "total_slots": len(items),
                "original_figure_bin": label,
                "included_in_original_figure": label != "missing",
                "raw_zero_call_prior_baseline": metadata.get("raw_zero_call_prior_baseline", ""),
                "filtered_to_prior_equivalent": metadata.get("filtered_to_prior_equivalent", ""),
            }
        )
        bucket = bin_summary[label]
        bucket["views"] += 1
        bucket["failed_views"] += int(failed)
        bucket["correct_frozen"] += frozen_correct
        bucket["correct_posthoc"] += corrected
        bucket["slots"] += len(items)
    write_csv(OUT / "call_count_views.csv", details)
    order = ["0", "1-5", "6-10", "11-20", "21-40", "41+", "missing"]
    summary_rows = []
    for label in order:
        value = bin_summary[label]
        summary_rows.append(
            {
                "bin": label,
                "views": value["views"],
                "failed_views": value["failed_views"],
                "correct_slots_frozen": value["correct_frozen"],
                "correct_slots_posthoc": value["correct_posthoc"],
                "total_slots": value["slots"],
                "included_in_original_figure": label != "missing",
            }
        )
    write_csv(OUT / "call_count_bins.csv", summary_rows)
    return {
        "view_count": len(details),
        "plotted_view_count": sum(row["views"] for row in summary_rows if row["bin"] != "missing"),
        "missing_view_count": bin_summary["missing"]["views"],
        "zero_view_count": bin_summary["0"]["views"],
        "zero_view_types": dict(zero_kinds),
    }


def build_profile_files() -> dict[str, Any]:
    report = json.loads((ROOT / "artifacts/profile_pool/generation_report.json").read_text(encoding="utf-8"))
    targets = json.loads(REAL_TARGETS_PATH.read_text(encoding="utf-8"))["constraints"]
    country = next(item for item in targets if item["name"] == "country_population")
    write_csv(
        OUT / "country_population_targets_current_file.csv",
        [
            {"country": name, "target_weight": country["targets"][name]}
            for name in COUNTRY_PROFILES
        ],
    )
    status = {
        "run_id": "profile_pool",
        "invocation_or_recovery_manifest_present": False,
        "lambda_target_proposal_mixture": {
            "value": report["seed_proposal"]["target_ratio_requested"],
            "status": "frozen in generation_report.json",
        },
        "candidate_pool_K": {
            "base_candidate_count": report["candidate_count"],
            "validated_pool_size": report["candidate_pool_size"],
            "status": "frozen in generation_report.json",
        },
        "seed": {"value": None, "status": "unrecoverable: absent from retained run artifacts"},
        "ipf_tolerance": {"value": None, "status": "unrecoverable: absent from retained run artifacts"},
        "stopping_condition": {
            "value": None,
            "status": "partially recoverable: 500 iterations and convergence flag are frozen; min-iterations/tolerance are not",
        },
        "ipf_iterations_observed": report["ipf"]["iterations"],
        "ipf_converged_flag": report["ipf"]["converged"],
        "ipf_final_max_l1": max(report["ipf"]["final_l1"].values()),
        "zero_support_handling_implementation": (
            "Positive target cells without candidates trigger targeted candidate generation; "
            "remaining unsupported cells abort IPF. Capacity is also checked against "
            "ceil(output_count * target), with targeted backfill or failure."
        ),
        "zero_support_run_outcome": report["backfill"],
        "weighted_sampling_implementation": (
            "PPS weighted sampling without replacement using exponential-race keys "
            "score=log(U)/weight; the implementation repeats sample_trials times and keeps "
            "the sample with minimum total marginal L1. The historical sample_trials value "
            "is not retained."
        ),
        "country_target_file": str(REAL_TARGETS_PATH.relative_to(ROOT)),
        "country_target_file_used_by_historical_run": (
            "unverifiable: retained run artifacts do not include the target-file path or hash"
        ),
    }
    (OUT / "profile_generation_recovery_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return status


def build_schema_file() -> None:
    rows = []
    deterministic = {
        "age": "Exact integer, or an explicit numeric interval containing the true age.",
        "sex": "Canonical male/female lexical aliases.",
        "ethnicity": "Canonical broad-category aliases.",
        "citizenship": "Country/demonym aliases for the same country.",
        "current_location": "Must contain the true city and canonical country.",
        "birth_location": "Must contain the true city and canonical country.",
        "education_level": "Canonical alias at the same completed level.",
        "income_level": "Canonical alias for the same five-level category.",
        "relationship_status": "Canonical alias for the same status.",
        "religious_belief": "Canonical alias for the same broad affiliation.",
        "occupation": "Normalized non-empty strings pass when either contains the other.",
        "physical_condition": "Exact normalized condition or canonical no-condition alias.",
        "mental_condition": "Exact normalized condition or canonical no-condition alias.",
        "name": "Normalized exact identifier match; no LLM Judge. Unicode empty/empty false positives are removed post hoc.",
        "email": "Normalized exact identifier match; no LLM Judge.",
        "phone_number": "Normalized exact identifier match; no LLM Judge.",
        "government_id": "Normalized exact identifier match; no LLM Judge.",
    }
    judge_set = set(ATTRIBUTE_RUBRICS)
    for attribute in FULL_SCHEMA_FIELDS:
        rows.append(
            {
                "attribute": attribute,
                "definition": ATTRIBUTE_DESCRIPTIONS[attribute],
                "deterministic_granularity": deterministic[attribute],
                "llm_judge_if_unresolved": attribute in judge_set,
                "llm_judge_granularity": ATTRIBUTE_RUBRICS.get(attribute, "Not used."),
                "missing_empty_uncertain": "incorrect",
            }
        )
    write_csv(OUT / "attribute_schema_and_scoring.csv", rows)


def build_prompt_and_serialization_files() -> dict[str, Any]:
    source = (ROOT / "agent_env/pydantic_agent.py").read_text(encoding="utf-8")
    match = re.search(r'DEFAULT_INSTRUCTIONS = """(.*?)"""\.strip\(\)', source, re.S)
    if match is None:
        raise RuntimeError("Cannot locate DEFAULT_INSTRUCTIONS")
    agent_prompt = match.group(1).strip()
    (OUT / "tool_agent_system_prompt_current.txt").write_text(
        agent_prompt + "\n", encoding="utf-8"
    )
    profiles = read_jsonl(PROFILES)
    example = dict(profiles[0])
    example["_candidate_id"] = "P0001"
    (OUT / "government_id_audit_prompt_P0001_current.txt").write_text(
        build_government_id_audit_prompt([example]) + "\n", encoding="utf-8"
    )
    selected_trajectories = []
    local = None
    for item in iter_jsonl(TRAJECTORIES):
        if item.get("scenario_id") == "S0198":
            local = item
        if item.get("profile_id") == "P0416":
            selected_trajectories.append(item)
    if local is None:
        raise RuntimeError("Missing local sample S0198")
    local_prompt, local_projected, local_metadata = build_observation_prompt_details(
        local["tools_info"],
        SERVER_VIEW,
        server_name="BioMCP",
        observation_format="lossless_tool_catalog",
    )
    (OUT / "local_view_S0198_BioMCP_prompt_current.txt").write_text(
        local_prompt + "\n", encoding="utf-8"
    )
    (OUT / "local_view_S0198_BioMCP_projected_calls.json").write_text(
        json.dumps(
            {"metadata": {**local_metadata, "local_projected_call_count": len(local_projected)}, "projected_calls": local_projected},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    profile_rows = selected_trajectories
    profile_rows.sort(key=lambda row: DOMAIN_ORDER.index(row["domain"]))
    observation, cross_metadata = cross_domain_observation(
        [
            {
                "domain": row["domain"],
                "scenario_id": row["scenario_id"],
                "tools_info": row["tools_info"],
                "observation_format": "lossless_tool_catalog",
            }
            for row in profile_rows
        ]
    )
    cross_prompt = ONE_SHOT_PROMPT_TEMPLATE.replace("${tools_info}", observation).replace(
        "${profile_attributes}", json.dumps(PROFILE_ATTRIBUTES, ensure_ascii=False)
    )
    (OUT / "cross_domain_P0416_prompt_current.txt").write_text(
        cross_prompt + "\n", encoding="utf-8"
    )
    return {
        "local_sample": {
            "scenario_id": "S0198",
            "server": "BioMCP",
            "local_call_orders": [item["call_order"] for item in local_projected],
        },
        "cross_domain_sample": {
            "profile_id": "P0416",
            "domain_order": list(DOMAIN_ORDER),
            "scenario_ids": [row["scenario_id"] for row in profile_rows],
            **cross_metadata,
        },
        "historical_prompt_hash_status": (
            "unrecoverable: trajectory/profile-generation manifests retaining prompt hashes are absent"
        ),
        "tool_agent_decoding": {
            "model_alias_in_trajectory": "deepseek-v4-flash",
            "provider_model_in_run_metadata": "deepseek-v4-flash-0731",
            "temperature": None,
            "top_p": None,
            "max_output_tokens": None,
            "status": "not explicitly passed by the adapter; historical provider defaults are unrecoverable",
        },
    }


def build_health_file() -> dict[str, Any]:
    profiles = read_jsonl(PROFILES)
    shown = {
        "physical_condition": {
            "None",
            "tension-type headache",
            "age-related and other hearing loss",
            "migraine",
            "chronic kidney disease",
            "low back pain",
            "type 2 diabetes",
            "osteoarthritis",
        },
        "mental_condition": {
            "None",
            "anxiety disorders",
            "depressive disorders",
            "other mental disorders",
            "idiopathic developmental intellectual disability",
            "alcohol use disorders",
            "schizophrenia",
        },
    }
    rows = []
    omitted: dict[str, list[str]] = {}
    for field in ("physical_condition", "mental_condition"):
        counts = Counter(str(row[field]) for row in profiles)
        omitted[field] = []
        for category, count in counts.most_common():
            is_shown = category in shown[field]
            if not is_shown:
                omitted[field].append(category)
            rows.append(
                {
                    "attribute": field,
                    "category": category,
                    "count": count,
                    "percentage": count / len(profiles) * 100.0,
                    "shown_in_current_figure": is_shown,
                }
            )
    write_csv(OUT / "health_condition_frequencies.csv", rows)
    return {
        "profiles": len(profiles),
        "physical_categories": sum(row["attribute"] == "physical_condition" for row in rows),
        "mental_categories": sum(row["attribute"] == "mental_condition" for row in rows),
        "omitted_categories": omitted,
        "label_cardinality": (
            "Both fields are scalar strings: exactly one physical label and one mental label per profile. "
            "A profile may simultaneously have one non-None physical and one non-None mental label."
        ),
    }


def iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def build_cost_and_execution_files() -> dict[str, Any]:
    runs = {
        "GPT-5.5": ROOT / "artifacts/privacy_experiments/gpt_5_5/full_trajectory/lossless_tool_catalog",
        "DeepSeek-V4-Flash-0731": CATALOG,
        "MiniMax-M3": ROOT / "artifacts/privacy_experiments/minimax_m3/full_trajectory/lossless_tool_catalog",
        "Gemini-3-Flash-Preview": ROOT / "artifacts/privacy_experiments/gemini_3_flash_preview/full_trajectory/lossless_tool_catalog",
    }
    prices = {
        "GPT-5.5": (5.0, 30.0, "standard"),
        "DeepSeek-V4-Flash-0731": (0.22, 0.66, "off-peak"),
        "MiniMax-M3": (0.3, 1.2, "standard <=512k"),
        "Gemini-3-Flash-Preview": (0.5, 3.0, "paid tier"),
    }
    legacy = {
        row["model"]: row
        for row in csv.DictReader(
            (ROOT / "results/paper/data/model_generalization_cost.csv").open(encoding="utf-8")
        )
    }
    output = []
    for label, root in runs.items():
        prompt = completion = total = usage_views = failures = 0
        models: Counter[str] = Counter()
        fingerprints: Counter[str] = Counter()
        for path in sorted(root.glob("S*.json")):
            if path.name.startswith("._"):
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            metadata = value.get("metadata", {})
            requests = metadata.get("requests", {})
            request = requests.get("one_shot", {}) if isinstance(requests, dict) else {}
            if not request and isinstance(metadata.get("request"), dict):
                request = metadata["request"]
            usage = request.get("usage", {}) if isinstance(request, dict) else {}
            if usage:
                usage_views += 1
            prompt += int(usage.get("prompt_tokens") or 0)
            completion += int(usage.get("completion_tokens") or 0)
            total += int(usage.get("total_tokens") or 0)
            failures += int(bool(metadata.get("failure_scored_as_incorrect")))
            models[str(metadata.get("model"))] += 1
            fingerprint = request.get("system_fingerprint")
            if fingerprint:
                fingerprints[str(fingerprint)] += 1
        input_price, output_price, basis = prices[label]
        actual_cost = prompt / 1_000_000 * input_price + completion / 1_000_000 * output_price
        legacy_row = legacy[label]
        manifest_path = root / "_recovery/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        output.append(
            {
                "model_label": label,
                "artifact_model_ids": " | ".join(sorted(models)),
                "views": 4000,
                "usage_recorded_views": usage_views,
                "failed_views": failures,
                "recorded_input_tokens": prompt,
                "recorded_completion_tokens": completion,
                "recorded_total_tokens": total,
                "legacy_estimated_output_tokens": legacy_row["estimated_output_tokens"],
                "input_price_per_m": input_price,
                "output_price_per_m": output_price,
                "price_basis": basis,
                "cost_from_recorded_usage_usd": actual_cost,
                "legacy_reported_cost_usd": legacy_row["estimated_cost_usd"],
                "run_started_utc": iso(manifest.get("started_at")),
                "run_completed_utc": iso(manifest.get("completed_at")),
                "system_fingerprints": " | ".join(
                    f"{name} ({count})" for name, count in sorted(fingerprints.items())
                ),
            }
        )
    write_csv(OUT / "recorded_token_usage_and_cost.csv", output)
    return {
        "pricing_date_stated_in_manuscript": "2026-08-22",
        "legacy_cost_file": "results/paper/data/model_generalization_cost.csv",
        "finding": (
            "The legacy file's estimated_output_tokens are far below the completion_tokens "
            "recorded by the API. API-cost reporting should use recorded completion tokens or "
            "explain and justify the alternative estimator."
        ),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    call_count = build_call_count_files()
    profile = build_profile_files()
    build_schema_file()
    prompts = build_prompt_and_serialization_files()
    health = build_health_file()
    cost = build_cost_and_execution_files()
    summary = {
        "schema_version": "1.0",
        "generated_from_frozen_artifacts": True,
        "call_count": call_count,
        "profile_generation": profile,
        "prompts_and_serialization": prompts,
        "health": health,
        "cost": cost,
    }
    (OUT / "audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote reproducibility audit bundle to {OUT}")


if __name__ == "__main__":
    main()
