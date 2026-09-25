#!/usr/bin/env python3
"""Compare attack models using only the unified Semantic ASR metric."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np


ARM_ORDER = (
    "schema_only",
    "metadata_sequence",
    "metadata_sequence_parameters",
    "metadata_sequence_results",
    "full_trajectory",
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def read_records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def parse_run(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("run must use LABEL=EXPERIMENT_ROOT")
    label, path = value.split("=", 1)
    if not label or not path:
        raise argparse.ArgumentTypeError("run must use LABEL=EXPERIMENT_ROOT")
    return label, Path(path)


def per_scenario_rates(
    records: list[dict[str, Any]],
) -> tuple[list[str], dict[str, np.ndarray]]:
    scenarios = sorted(
        {
            str(record["scenario_id"])
            for record in records
            if record["arm"] == "schema_only"
        }
    )
    index = {scenario: offset for offset, scenario in enumerate(scenarios)}
    counts = {
        arm: np.zeros(len(scenarios), dtype=int) for arm in ARM_ORDER
    }
    correct = {
        arm: np.zeros(len(scenarios), dtype=float) for arm in ARM_ORDER
    }
    for record in records:
        arm = str(record["arm"])
        if arm not in counts:
            continue
        offset = index[str(record["scenario_id"])]
        counts[arm][offset] += 1
        correct[arm][offset] += float(bool(record["semantic"]))
    for arm in ARM_ORDER:
        if np.any(counts[arm] != 17):
            raise ValueError(f"Expected 17 slots per scenario for {arm}")
        correct[arm] /= counts[arm]
    return scenarios, correct


def build_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# 跨模型统一 Semantic ASR 比较",
        "",
        f"固定 Judge：`{analysis['judge_model']}`  ",
        f"每个模型的配对轨迹数：{analysis['trajectory_count']}  ",
        "本实验只报告一个正确率指标：Semantic ASR。",
        "",
        "## 主要结果",
        "",
        "| 攻击模型 | Schema ASR | Full ASR | 轨迹增量 | Server ASR | Server 增量 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in analysis["models"]:
        lines.append(
            f"| {item['attack_model']} | "
            f"{item['schema_asr']['estimate_pct']:.3f}% | "
            f"{item['full_asr']['estimate_pct']:.3f}% | "
            f"{item['trajectory_lift']['estimate_pp']:+.3f} pp | "
            f"{item['server_asr']['estimate_pct']:.3f}% | "
            f"{item['server_lift']['estimate_pp']:+.3f} pp |"
        )
    lines.extend(
        [
            "",
            "## 模型间观察差异",
            "",
            "正值表示左侧模型从轨迹中恢复的可归因信息更多。",
            "",
            "| 模型比较 | 轨迹增量差异 |",
            "|---|---:|",
        ]
    )
    for item in analysis["pairwise_trajectory_lift"]:
        lines.append(
            f"| {item['left_model']} − {item['right_model']} | "
            f"{item['difference_pp']:+.3f} pp |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            f"- {analysis['trajectory_count']} 条轨迹覆盖同一批 Profile，并在模型间逐条配对。",
            "- 所有攻击模型使用相同固定 Judge 和相同评分契约。",
            "- 若 Judge 同时也是某个被比较的攻击模型，该模型可能存在自评偏差。",
            "- API 重试后最终成功的样本不会从分母中删除。",
            "- 本报告只描述固定 Benchmark 上的精确计数、ASR 和百分点差。",
            "",
        ]
    )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        type=parse_run,
        required=True,
        help=(
            "Repeat LABEL=ANALYSIS_DIR for every model. For backward "
            "compatibility, an experiment root containing analysis/ is also accepted."
        ),
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if len(args.run) < 2:
        raise SystemExit("At least two --run arguments are required")
    model_rows: list[dict[str, Any]] = []
    lift_arrays: dict[str, np.ndarray] = {}
    reference_scenarios: list[str] | None = None
    judge_models: set[str] = set()

    for label, root in args.run:
        root = root.resolve()
        analysis_dir = (
            root
            if (root / "semantic_summary.json").is_file()
            else root / "analysis"
        )
        summary = read_json(analysis_dir / "semantic_summary.json")
        records = read_records(analysis_dir / "semantic_records.jsonl")
        scenarios, arm_rates = per_scenario_rates(records)
        if reference_scenarios is None:
            reference_scenarios = scenarios
        elif scenarios != reference_scenarios:
            raise ValueError(f"Scenario mismatch for {label}")
        attack_model = str(summary["attack_model"])
        judge_models.add(str(summary["judge_model"]))
        lift_arrays[attack_model] = (
            arm_rates["full_trajectory"] - arm_rates["schema_only"]
        )
        schema_pct = float(
            summary["arms"]["schema_only"]["overall"]["semantic_pct"]
        )
        full_pct = float(
            summary["arms"]["full_trajectory"]["overall"]["semantic_pct"]
        )
        trajectory_lift = round(
            float(np.mean(lift_arrays[attack_model])) * 100.0, 3
        )
        server_pct = float(summary["server_views"]["micro_overall"]["semantic_pct"])
        server_lift = float(summary["server_views"]["semantic_lift_pp"])
        model_rows.append(
            {
                "label": label,
                "attack_model": attack_model,
                "schema_asr": {"estimate_pct": schema_pct},
                "full_asr": {"estimate_pct": full_pct},
                "trajectory_lift": {"estimate_pp": trajectory_lift},
                "server_asr": {"estimate_pct": server_pct},
                "server_lift": {"estimate_pp": server_lift},
                "judge_unique_cases": summary["judge_diagnostics"][
                    "expected_unique_cases"
                ],
                "judge_consensus_correct_cases": summary["judge_diagnostics"][
                    "adjudication"
                ]["consensus_correct_cases"],
            }
        )
    if len(judge_models) != 1:
        raise ValueError(f"Judge model mismatch: {sorted(judge_models)}")

    pairwise: list[dict[str, Any]] = []
    for left, right in combinations(model_rows, 2):
        left_model = left["attack_model"]
        right_model = right["attack_model"]
        differences = lift_arrays[left_model] - lift_arrays[right_model]
        pairwise.append(
            {
                "left_model": left_model,
                "right_model": right_model,
                "difference_pp": round(
                    float(np.mean(differences)) * 100.0, 3
                ),
            }
        )

    analysis = {
        "schema_version": "1.0",
        "experiment": "cross_model_semantic_asr_comparison",
        "metric": "semantic_asr",
        "analysis_type": "fixed_benchmark_descriptive",
        "judge_model": next(iter(judge_models)),
        "trajectory_count": len(reference_scenarios or []),
        "models": model_rows,
        "pairwise_trajectory_lift": pairwise,
        "notes": [
            "All models use one unified Semantic ASR metric.",
            "All models are evaluated on the same stratified trajectories.",
            "Pairwise differences are exact observed benchmark differences.",
        ],
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.markdown_output.write_text(
        build_markdown(analysis),
        encoding="utf-8",
    )
    print(f"Comparison JSON: {args.json_output}")
    print(f"Comparison report: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
