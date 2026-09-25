#!/usr/bin/env python3
"""Compare single-arm attack models under one fixed first-pass semantic judge."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from privacy_trace.hybrid_judge import semantic_correct, unique_cases


def parse_run(value: str) -> tuple[str, Path, Path]:
    if "=" not in value or "::" not in value:
        raise argparse.ArgumentTypeError(
            "run must use LABEL=RECORDS_JSONL::JUDGE_ROOT"
        )
    label, paths = value.split("=", 1)
    records, judge = paths.split("::", 1)
    if not label or not records or not judge:
        raise argparse.ArgumentTypeError(
            "run must use LABEL=RECORDS_JSONL::JUDGE_ROOT"
        )
    return label, Path(records), Path(judge)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"no records in {path}")
    return rows


def load_judgments(root: Path) -> tuple[dict[str, dict[str, Any]], str]:
    judgments: dict[str, dict[str, Any]] = {}
    models: set[str] = set()
    for path in sorted((root / "batches").rglob("*.json")):
        batch = json.loads(path.read_text())
        if batch.get("model"):
            models.add(str(batch["model"]))
        for item in batch.get("judgments", []):
            case_id = str(item["case_id"])
            if case_id in judgments and judgments[case_id] != item:
                raise ValueError(f"conflicting judgment for {case_id}")
            judgments[case_id] = item
    if len(models) != 1:
        raise ValueError(f"expected one judge model in {root}, got {sorted(models)}")
    return judgments, next(iter(models))


def clustered_interval(
    numerators: list[float],
    denominators: list[float],
    *,
    samples: int,
    seed: int,
) -> list[float]:
    cluster_values = [
        numerator / denominator
        for numerator, denominator in zip(numerators, denominators, strict=True)
    ]
    rng = random.Random(seed)
    count = len(cluster_values)
    values = sorted(
        sum(cluster_values[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(samples)
    )
    return [
        round(values[int(0.025 * samples)] * 100.0, 3),
        round(values[int(0.975 * samples) - 1] * 100.0, 3),
    ]


def score_run(
    label: str,
    records_path: Path,
    judge_root: Path,
    *,
    samples: int,
    seed: int,
) -> tuple[dict[str, Any], dict[tuple[str, str], bool], dict[str, str]]:
    records = read_jsonl(records_path)
    if len(records) != 68_000:
        raise ValueError(f"{label}: expected 68000 slots, got {len(records)}")
    if {str(row.get("arm")) for row in records} != {"full_trajectory"}:
        raise ValueError(f"{label}: records are not the single full_trajectory arm")
    judgments, judge_model = load_judgments(judge_root)
    expected_ids = {str(case["case_id"]) for case in unique_cases(records)}
    if expected_ids != set(judgments):
        raise ValueError(
            f"{label}: judge coverage mismatch "
            f"missing={len(expected_ids - set(judgments))} "
            f"extra={len(set(judgments) - expected_ids)}"
        )

    by_profile_correct: Counter[str] = Counter()
    by_profile_total: Counter[str] = Counter()
    by_domain_correct: Counter[str] = Counter()
    by_domain_total: Counter[str] = Counter()
    failed_views: set[str] = set()
    scored: dict[tuple[str, str], bool] = {}
    scenario_profiles: dict[str, str] = {}
    source_counts: Counter[str] = Counter()
    for row in records:
        correct, source, _ = semantic_correct(row, judgments)
        scenario = str(row["scenario_id"])
        attribute = str(row["attribute"])
        profile = str(row["profile_id"])
        domain = str(row["domain"])
        scored[(scenario, attribute)] = bool(correct)
        scenario_profiles[scenario] = profile
        by_profile_correct[profile] += int(correct)
        by_profile_total[profile] += 1
        by_domain_correct[domain] += int(correct)
        by_domain_total[domain] += 1
        source_counts[source] += 1
        if row.get("generation_failed"):
            failed_views.add(scenario)

    profiles = sorted(by_profile_total)
    numerators = [float(by_profile_correct[p]) for p in profiles]
    denominators = [float(by_profile_total[p]) for p in profiles]
    correct = int(sum(numerators))
    total = int(sum(denominators))
    summary = {
        "label": label,
        "judge_model": judge_model,
        "profiles": len(profiles),
        "trajectories": len(scenario_profiles),
        "slots": total,
        "correct": correct,
        "asr_pct": round(correct / total * 100.0, 3),
        "ci95_pct": clustered_interval(
            numerators, denominators, samples=samples, seed=seed
        ),
        "generation_failed_views": len(failed_views),
        "generation_failure_pct": round(
            len(failed_views) / len(scenario_profiles) * 100.0, 3
        ),
        "judge_unique_cases": len(expected_ids),
        "semantic_source_counts": dict(source_counts),
        "per_domain": {
            domain: {
                "correct": by_domain_correct[domain],
                "slots": by_domain_total[domain],
                "asr_pct": round(
                    by_domain_correct[domain] / by_domain_total[domain] * 100.0,
                    3,
                ),
            }
            for domain in sorted(by_domain_total)
        },
    }
    return summary, scored, scenario_profiles


def build_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# Model generalization: catalog full-trajectory attack",
        "",
        f"Fixed first-pass judge: `{analysis['judge_model']}`. Bootstrap: "
        f"{analysis['bootstrap']['samples']} profile-clustered resamples, "
        f"seed {analysis['bootstrap']['seed']}.",
        "",
        "| Attack model | Correct / 68,000 | ASR | 95% CI | Generation failures |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in analysis["models"]:
        lines.append(
            f"| {row['label']} | {row['correct']} / {row['slots']} | "
            f"{row['asr_pct']:.3f}% | [{row['ci95_pct'][0]:.3f}, "
            f"{row['ci95_pct'][1]:.3f}] | "
            f"{row['generation_failed_views']} / {row['trajectories']} |"
        )
    lines.extend(
        [
            "",
            "| Paired contrast | Difference | 95% CI |",
            "|---|---:|---:|",
        ]
    )
    for row in analysis["pairwise"]:
        lines.append(
            f"| {row['left']} − {row['right']} | {row['difference_pp']:+.3f} pp | "
            f"[{row['ci95_pp'][0]:+.3f}, {row['ci95_pp'][1]:+.3f}] |"
        )
    lines.extend(["", "## Domains", ""])
    domains = sorted(analysis["models"][0]["per_domain"])
    lines.append("| Attack model | " + " | ".join(domains) + " |")
    lines.append("|---|" + "---:|" * len(domains))
    for row in analysis["models"]:
        lines.append(
            f"| {row['label']} | "
            + " | ".join(f"{row['per_domain'][d]['asr_pct']:.3f}%" for d in domains)
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=parse_run, required=True)
    parser.add_argument("--samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()

    summaries: list[dict[str, Any]] = []
    scores: dict[str, dict[tuple[str, str], bool]] = {}
    profile_maps: dict[str, dict[str, str]] = {}
    judge_models: set[str] = set()
    for label, records, judge in args.run:
        summary, run_scores, profile_map = score_run(
            label,
            records.resolve(),
            judge.resolve(),
            samples=args.samples,
            seed=args.seed,
        )
        summaries.append(summary)
        scores[label] = run_scores
        profile_maps[label] = profile_map
        judge_models.add(summary["judge_model"])
    if len(judge_models) != 1:
        raise ValueError(f"judge mismatch: {sorted(judge_models)}")

    pairwise: list[dict[str, Any]] = []
    for left, right in combinations([row["label"] for row in summaries], 2):
        if set(scores[left]) != set(scores[right]):
            raise ValueError(f"slot mismatch: {left} vs {right}")
        if profile_maps[left] != profile_maps[right]:
            raise ValueError(f"scenario/profile mismatch: {left} vs {right}")
        by_profile_delta: Counter[str] = Counter()
        by_profile_total: Counter[str] = Counter()
        for key, left_value in scores[left].items():
            profile = profile_maps[left][key[0]]
            by_profile_delta[profile] += int(left_value) - int(scores[right][key])
            by_profile_total[profile] += 1
        profiles = sorted(by_profile_total)
        numerators = [float(by_profile_delta[p]) for p in profiles]
        denominators = [float(by_profile_total[p]) for p in profiles]
        pairwise.append(
            {
                "left": left,
                "right": right,
                "difference_pp": round(
                    float(sum(numerators) / sum(denominators) * 100.0), 3
                ),
                "ci95_pp": clustered_interval(
                    numerators,
                    denominators,
                    samples=args.samples,
                    seed=args.seed,
                ),
            }
        )

    analysis = {
        "schema_version": "1.0",
        "experiment": "model_generalization_catalog_full_trajectory_first_pass",
        "judge_model": next(iter(judge_models)),
        "bootstrap": {
            "method": "profile-clustered percentile",
            "samples": args.samples,
            "seed": args.seed,
        },
        "models": summaries,
        "pairwise": pairwise,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    args.markdown_output.write_text(build_markdown(analysis), encoding="utf-8")
    print(json.dumps(analysis, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
