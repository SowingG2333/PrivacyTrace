#!/usr/bin/env python3
"""Analyze first-pass Semantic ASR for the 2x2 parameter/result ablation."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import statistics
import sys
from typing import Any


PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from privacy_trace.hybrid_judge import semantic_correct  # noqa: E402
from scripts.analyze_privacy_tool_leakage import load_judgments  # noqa: E402


ARM_ORDER = (
    "schema_prior",
    "metadata_sequence",
    "metadata_sequence_parameters",
    "metadata_sequence_results",
    "catalog_one_shot",
)


def load_scored(
    path: Path,
    arms: set[str],
    judgments: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record["arm"] not in arms:
                continue
            correct, source, case_id = semantic_correct(record, judgments)
            record["semantic"] = bool(correct)
            record["semantic_source"] = source
            record["judge_case_id"] = case_id
            output.append(record)
    return output


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    correct = sum(bool(row["semantic"]) for row in rows)
    views = {str(row["view_id"]) for row in rows}
    failed = {
        str(row["view_id"])
        for row in rows
        if bool(row.get("generation_failed"))
    }
    return {
        "slots": len(rows),
        "correct": correct,
        "asr_pct": round(100 * correct / len(rows), 3),
        "views": len(views),
        "generation_failed_views": len(failed),
    }


def profile_values(rows: list[dict[str, Any]]) -> dict[str, float]:
    groups: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        groups[str(row["profile_id"])].append(bool(row["semantic"]))
    return {key: sum(values) / len(values) for key, values in groups.items()}


def bootstrap_contrast(
    coefficients: dict[str, float],
    by_arm: dict[str, list[dict[str, Any]]],
    *,
    samples: int = 10_000,
    seed: int = 20260813,
) -> dict[str, Any]:
    values = {arm: profile_values(by_arm[arm]) for arm in coefficients}
    profiles = sorted(set.intersection(*(set(item) for item in values.values())))
    contrasts = [
        sum(coefficient * values[arm][profile] for arm, coefficient in coefficients.items())
        for profile in profiles
    ]
    point = statistics.fmean(contrasts)
    rng = random.Random(seed)
    count = len(contrasts)
    boot = sorted(
        sum(contrasts[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(samples)
    )
    return {
        "profiles": count,
        "delta_pp": round(100 * point, 3),
        "ci95_pp": [
            round(100 * boot[int(0.025 * samples)], 3),
            round(100 * boot[min(samples - 1, int(0.975 * samples))], 3),
        ],
        "method": "profile-clustered percentile bootstrap",
        "samples": samples,
        "coefficients": coefficients,
    }


def placeholder_ids(root: Path) -> set[str]:
    result = set()
    for path in (root / "batches").rglob("*.json"):
        if path.name.startswith("._"):
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if not (value.get("metadata") or {}).get("failure_scored_as_incorrect"):
            continue
        result.update(str(item["case_id"]) for item in value["judgments"])
    return result


def report(value: dict[str, Any]) -> str:
    arms = value["arms"]
    effects = value["effects"]
    lines = [
        "# Channel ablation: first-pass Semantic ASR",
        "",
        "所有 arm 使用相同的 4,000 trajectories 和固定 17 属性分母。",
        "",
        "## Overall",
        "",
        "| Arm | View | ASR | Correct / slots | Generation failures |",
        "|---|---|---:|---:|---:|",
    ]
    labels = {
        "schema_prior": "Schema only",
        "metadata_sequence": "Metadata + sequence",
        "metadata_sequence_parameters": "Metadata + sequence + parameters",
        "metadata_sequence_results": "Metadata + sequence + results",
        "catalog_one_shot": "Full catalog",
    }
    for arm in ARM_ORDER:
        row = arms[arm]["overall"]
        lines.append(
            f"| {arm} | {labels[arm]} | {row['asr_pct']:.3f}% | "
            f"{row['correct']} / {row['slots']} | {row['generation_failed_views']} / {row['views']} |"
        )
    lines.extend(["", "## Paired effects", "", "| Effect | Delta | 95% CI |", "|---|---:|---:|"])
    for name, row in effects.items():
        lines.append(
            f"| {name} | {row['delta_pp']:+.3f} pp | "
            f"[{row['ci95_pp'][0]:+.3f}, {row['ci95_pp'][1]:+.3f}] |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Metadata effect compares tool/server metadata and order against schema-only prior.",
            "- Parameter and result effects add one channel at a time to the same metadata/sequence base.",
            "- Interaction is `Full - Metadata+Parameters - Metadata+Results + Metadata`; it measures non-additive joint signal.",
            "- Each arm is an independent temperature-1 generation, so small contrasts also contain attack-generation variance.",
            "- Semantic scores use the same single-pass hybrid Judge protocol as the main experiment; uncertain and exhausted cases count as incorrect.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    experiment = PROJECT / "artifacts/privacy_experiments/deepseek_v4_flash_0731"
    original_records = experiment / "evaluation/attack_records.jsonl"
    channel_root = experiment / "evaluation/channel_ablation"
    channel_records = channel_root / "attack_records.jsonl"
    original_judge_root = experiment / "evaluation/judge_first"
    channel_judge_root = channel_root / "judge_first"
    output_root = channel_root / "analysis"

    original_judgments, original_models = load_judgments(original_judge_root)
    channel_judgments, channel_models = load_judgments(channel_judge_root)
    rows = load_scored(
        original_records,
        {"schema_prior", "catalog_one_shot"},
        original_judgments,
    )
    rows.extend(
        load_scored(
            channel_records,
            {
                "metadata_sequence",
                "metadata_sequence_parameters",
                "metadata_sequence_results",
            },
            channel_judgments,
        )
    )
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_arm[str(row["arm"])].append(row)
    for arm in ARM_ORDER:
        if len(by_arm[arm]) != 68_000:
            raise ValueError(f"{arm}: expected 68000 slots, got {len(by_arm[arm])}")

    arm_results = {}
    for arm in ARM_ORDER:
        domain_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in by_arm[arm]:
            domain_groups[str(row["domain"])].append(row)
        arm_results[arm] = {
            "overall": summarize(by_arm[arm]),
            "per_domain": {
                domain: summarize(items)
                for domain, items in sorted(domain_groups.items())
            },
            "semantic_sources": dict(Counter(row["semantic_source"] for row in by_arm[arm])),
        }

    effects = {
        "full_minus_schema": bootstrap_contrast(
            {"catalog_one_shot": 1, "schema_prior": -1}, by_arm
        ),
        "metadata_minus_schema": bootstrap_contrast(
            {"metadata_sequence": 1, "schema_prior": -1}, by_arm, seed=20260814
        ),
        "parameter_increment": bootstrap_contrast(
            {"metadata_sequence_parameters": 1, "metadata_sequence": -1}, by_arm, seed=20260815
        ),
        "result_increment": bootstrap_contrast(
            {"metadata_sequence_results": 1, "metadata_sequence": -1}, by_arm, seed=20260816
        ),
        "parameter_result_interaction": bootstrap_contrast(
            {
                "catalog_one_shot": 1,
                "metadata_sequence_parameters": -1,
                "metadata_sequence_results": -1,
                "metadata_sequence": 1,
            },
            by_arm,
            seed=20260817,
        ),
    }
    placeholders = placeholder_ids(channel_judge_root)
    result = {
        "schema_version": "1.0",
        "experiment": "channel_ablation_first_pass_semantic_asr",
        "attack_model": "deepseek-v4-flash-0731",
        "judge_models": sorted(original_models | channel_models),
        "arms": arm_results,
        "effects": effects,
        "judge": {
            "channel_unique_judgments": len(channel_judgments),
            "materialized_failure_unique_cases": len(placeholders),
            "materialized_failure_batches": sum(
                1
                for path in (channel_judge_root / "batches").rglob("*.json")
                if not path.name.startswith("._")
                and (json.loads(path.read_text(encoding="utf-8")).get("metadata") or {}).get("failure_scored_as_incorrect")
            ),
        },
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "channel_semantic_asr.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_root / "channel_semantic_asr.md").write_text(report(result), encoding="utf-8")
    print(json.dumps({
        "arms": {arm: arm_results[arm]["overall"]["asr_pct"] for arm in ARM_ORDER},
        "effects": {name: row for name, row in effects.items()},
        "judge": result["judge"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
