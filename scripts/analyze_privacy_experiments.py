#!/usr/bin/env python3
"""Aggregate Privacy Attack V1 Semantic ASR and clustered bootstrap CIs."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from privacy_trace.hybrid_judge import unique_cases  # noqa: E402
from scripts.analyze_semantic_asr import (  # noqa: E402
    attach_semantic_scores, consensus_judgments, load_judgments,
)


def read_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    correct = sum(bool(row.get("semantic")) for row in rows)
    return {"slots": len(rows), "correct": correct, "asr": correct / len(rows) if rows else 0.0,
            "asr_pct": round(100 * correct / len(rows), 3) if rows else 0.0,
            "generation_failed": sum(bool(row.get("generation_failed")) for row in rows)}


def grouped(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(key))].append(row)
    return {name: summary(value) for name, value in sorted(buckets.items())}


def request_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    views: dict[str, dict[str, Any]] = {}
    for row in rows:
        views.setdefault(str(row["view_id"]), row)
    values = sorted(float(row["prompt_chars"]) for row in views.values() if row.get("prompt_chars") is not None)
    def percentile(q: float) -> float | None:
        if not values:
            return None
        return values[min(len(values) - 1, round((len(values) - 1) * q))]
    return {
        "views": len(views), "prompt_chars": {f"p{int(q * 100)}": percentile(q) for q in (.5, .9, .95, .99)},
        "generation_failure_rate": sum(bool(row.get("generation_failed")) for row in views.values()) / len(views) if views else 0.0,
        "length_finish_rate": sum(row.get("finish_reason") == "length" for row in views.values()) / len(views) if views else 0.0,
    }


def profile_values(rows: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        values[str(row["profile_id"])].append(bool(row["semantic"]))
    return {profile: sum(items) / len(items) for profile, items in values.items()}


def paired_bootstrap(left: list[dict[str, Any]], right: list[dict[str, Any]], *, seed: int = 42, samples: int = 10_000) -> dict[str, Any]:
    a, b = profile_values(left), profile_values(right)
    profiles = sorted(set(a) & set(b))
    if not profiles:
        return {"profiles": 0, "delta_pp": None, "ci95_pp": [None, None]}
    point = sum(a[p] - b[p] for p in profiles) / len(profiles)
    rng = random.Random(seed)
    values = []
    for _ in range(samples):
        draw = [profiles[rng.randrange(len(profiles))] for _ in profiles]
        values.append(sum(a[p] - b[p] for p in draw) / len(draw))
    values.sort()
    return {"profiles": len(profiles), "delta_pp": round(point * 100, 3),
            "ci95_pp": [round(values[int(.025 * samples)] * 100, 3), round(values[int(.975 * samples) - 1] * 100, 3)]}


def paired_rows(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Join two trajectory-level arms on the same scenario and attribute."""
    def index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (str(row.get("scenario_id")), str(row["attribute"]))
            if key in result:
                raise ValueError(f"duplicate paired record key: {key}")
            result[key] = row
        return result
    a, b = index(left), index(right)
    if set(a) != set(b):
        raise ValueError(
            f"paired coverage mismatch: left_only={len(set(a) - set(b))} "
            f"right_only={len(set(b) - set(a))}"
        )
    return [(a[key], b[key]) for key in sorted(a)]


def paired_outcomes(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, int]:
    pairs = paired_rows(left, right)
    outcomes = {"both_correct": 0, "left_only": 0, "right_only": 0, "both_wrong": 0}
    for left_row, right_row in pairs:
        left_correct, right_correct = bool(left_row["semantic"]), bool(right_row["semantic"])
        if left_correct and right_correct:
            outcomes["both_correct"] += 1
        elif left_correct:
            outcomes["left_only"] += 1
        elif right_correct:
            outcomes["right_only"] += 1
        else:
            outcomes["both_wrong"] += 1
    return {"slots": len(pairs), **outcomes}


def two_stage_metrics(stage1: list[dict[str, Any]], final: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = paired_rows(final, stage1)
    unresolved = [
        (final_row, stage1_row)
        for final_row, stage1_row in pairs
        if stage1_row.get("prediction_status") != "inferred"
    ]
    added = sum(
        bool(final_row["semantic"]) and not bool(stage1_row["semantic"])
        for final_row, stage1_row in pairs
    )
    filled_correct = sum(bool(final_row["semantic"]) for final_row, _ in unresolved)
    return {
        "stage1_fixed_denominator": summary(stage1),
        "final": summary(final),
        "stage2_added_correct_slots": added,
        "stage1_unresolved_slots": len(unresolved),
        "unresolved_slot_fill_accuracy": (
            filled_correct / len(unresolved) if unresolved else 0.0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--judge-root", type=Path, required=True)
    parser.add_argument("--verification-root", type=Path, required=True)
    parser.add_argument("--adjudication-root", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    records = read_records(args.records.resolve())
    first, first_models = load_judgments(args.judge_root.resolve())
    second, second_models = load_judgments(args.verification_root.resolve())
    third, third_models = load_judgments(args.adjudication_root.resolve())
    if first_models != second_models or first_models != third_models:
        raise SystemExit("judge model mismatch across rounds")
    verified, verification_diag = consensus_judgments(first, second)
    judgments, adjudication_diag = consensus_judgments(verified, third)
    judge_diag = attach_semantic_scores(records, judgments)
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_arm[str(row["arm"])].append(row)
    arms = {name: {"overall": summary(rows), "attribute": grouped(rows, "attribute"), "domain": grouped(rows, "domain")} for name, rows in sorted(by_arm.items())}
    comparisons: dict[str, Any] = {}
    for left_name, right_name in (
        ("catalog_one_shot", "schema_prior"),
        ("catalog_two_stage_final", "catalog_one_shot"),
        ("catalog_two_stage_final", "catalog_two_stage_stage1"),
        ("raw_one_shot", "catalog_one_shot"),
        ("cross_domain_all_servers", "catalog_one_shot"),
        ("cross_domain_all_servers", "schema_prior"),
    ):
        if left_name in by_arm and right_name in by_arm:
            comparisons[f"{left_name}_minus_{right_name}"] = paired_bootstrap(
                by_arm[left_name], by_arm[right_name], seed=args.seed, samples=args.bootstrap_samples
            )
    server_rows = by_arm.get("single_server", [])
    raw_catalog_outcomes = (
        paired_outcomes(by_arm["raw_one_shot"], by_arm["catalog_one_shot"])
        if "raw_one_shot" in by_arm and "catalog_one_shot" in by_arm
        else None
    )
    stage_metrics = (
        two_stage_metrics(by_arm["catalog_two_stage_stage1"], by_arm["catalog_two_stage_final"])
        if "catalog_two_stage_stage1" in by_arm and "catalog_two_stage_final" in by_arm
        else None
    )
    analysis = {
        "metric": "semantic_asr", "record_count": len(records),
        "unique_semantic_cases": len(unique_cases(records)), "judge_models": sorted(first_models),
        "arms": arms, "comparisons": comparisons,
        "single_server": {"micro": summary(server_rows), "by_server": grouped(server_rows, "server_name")},
        "two_stage": stage_metrics,
        "raw_vs_catalog_paired_outcomes": raw_catalog_outcomes,
        "pipeline_diagnostics": {
            name: request_diagnostics(by_arm[name])
            for name in ("raw_one_shot", "catalog_one_shot") if name in by_arm
        },
        "judge_diagnostics": {**judge_diag, "verification": verification_diag, "adjudication": adjudication_diag},
        "bootstrap": {"samples": args.bootstrap_samples, "seed": args.seed, "method": "profile_clustered_percentile"},
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Privacy Attack V1", "", "## Semantic ASR", "", "| Arm | ASR | Correct / slots |", "|---|---:|---:|"]
    for name, value in arms.items():
        metric = value["overall"]
        lines.append(f"| {name} | {metric['asr_pct']:.3f}% | {metric['correct']} / {metric['slots']} |")
    lines.extend(["", "## Profile-clustered paired differences", "", "| Comparison | Delta pp | 95% CI |", "|---|---:|---:|"])
    for name, value in comparisons.items():
        lines.append(f"| {name} | {value['delta_pp']:+.3f} | [{value['ci95_pp'][0]}, {value['ci95_pp'][1]}] |")
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
