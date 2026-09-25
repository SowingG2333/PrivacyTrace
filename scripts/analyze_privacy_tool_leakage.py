#!/usr/bin/env python3
"""Analyze privacy leakage across tool-call views, domains, and ablations."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import statistics
import sys
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from scripts.analyze_semantic_asr import attach_semantic_scores  # noqa: E402


ARM_ORDER = (
    "schema_prior",
    "catalog_one_shot",
    "raw_one_shot",
    "catalog_two_stage_stage1",
    "catalog_two_stage_final",
    "cross_domain_all_servers",
    "single_server",
)


def read_records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_judgments(path: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Load real batch files while excluding AppleDouble and control artifacts."""
    judgments: dict[str, dict[str, Any]] = {}
    models: set[str] = set()
    paths = sorted((path / "batches").glob("**/[!._]*.json"))
    if not paths:
        raise ValueError(f"No judge batch outputs found in {path}")
    for batch_path in paths:
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        if not isinstance(batch, dict) or not isinstance(batch.get("judgments"), list):
            raise ValueError(f"Invalid judge batch: {batch_path}")
        if batch.get("model"):
            models.add(str(batch["model"]))
        for item in batch["judgments"]:
            if not isinstance(item, dict) or not item.get("case_id"):
                raise ValueError(f"Invalid judgment in {batch_path}")
            case_id = str(item["case_id"])
            if case_id in judgments and judgments[case_id] != item:
                raise ValueError(f"Conflicting judgment for case {case_id}")
            judgments[case_id] = item
    return judgments, models


def pct(numerator: int | float, denominator: int | float) -> float:
    return round(100.0 * numerator / denominator, 3) if denominator else 0.0


def summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(rows)
    correct = sum(bool(row.get("semantic")) for row in values)
    views = {str(row.get("view_id")) for row in values}
    failed_views = {
        str(row.get("view_id"))
        for row in values
        if bool(row.get("generation_failed"))
    }
    return {
        "slots": len(values),
        "correct": correct,
        "asr_pct": pct(correct, len(values)),
        "views": len(views),
        "profiles": len({str(row.get("profile_id")) for row in values}),
        "generation_failed_views": len(failed_views),
        "generation_failure_pct": pct(len(failed_views), len(views)),
    }


def group_rows(
    rows: Iterable[dict[str, Any]], key: str
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[str(row.get(key))].append(row)
    return dict(result)


def grouped_summary(
    rows: Iterable[dict[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    return {
        name: summary(items)
        for name, items in sorted(group_rows(rows, key).items())
    }


def profile_values(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    groups: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        groups[str(row["profile_id"])].append(bool(row["semantic"]))
    return {
        profile: sum(values) / len(values) for profile, values in groups.items()
    }


def clustered_delta(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    left_values = profile_values(left)
    right_values = profile_values(right)
    profiles = sorted(set(left_values) & set(right_values))
    if not profiles:
        return {"profiles": 0, "delta_pp": None, "ci95_pp": [None, None]}
    deltas = [left_values[key] - right_values[key] for key in profiles]
    point = statistics.fmean(deltas)
    rng = random.Random(seed)
    boot = []
    count = len(deltas)
    for _ in range(samples):
        boot.append(
            sum(deltas[rng.randrange(count)] for _ in range(count)) / count
        )
    boot.sort()
    low = boot[int(0.025 * samples)]
    high = boot[min(samples - 1, int(0.975 * samples))]
    return {
        "profiles": len(profiles),
        "delta_pp": round(100.0 * point, 3),
        "ci95_pp": [round(100.0 * low, 3), round(100.0 * high, 3)],
        "method": "profile-clustered percentile bootstrap",
        "samples": samples,
    }


def paired_outcomes(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> dict[str, int]:
    def index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], bool]:
        return {
            (str(row.get("scenario_id")), str(row["attribute"])): bool(
                row["semantic"]
            )
            for row in rows
        }

    left_index = index(left)
    right_index = index(right)
    if set(left_index) != set(right_index):
        raise ValueError("paired outcome coverage mismatch")
    output = Counter()
    for key in left_index:
        left_correct = left_index[key]
        right_correct = right_index[key]
        if left_correct and right_correct:
            output["both_correct"] += 1
        elif left_correct:
            output["left_only"] += 1
        elif right_correct:
            output["right_only"] += 1
        else:
            output["both_wrong"] += 1
    return {"slots": len(left_index), **dict(output)}


def quantiles(values: list[float]) -> dict[str, float | None]:
    values = sorted(values)
    if not values:
        return {"p50": None, "p90": None, "p95": None, "p99": None}
    return {
        name: round(values[min(len(values) - 1, round(q * (len(values) - 1)))], 3)
        for name, q in (("p50", 0.5), ("p90", 0.9), ("p95", 0.95), ("p99", 0.99))
    }


def request_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    views: dict[str, dict[str, Any]] = {}
    for row in rows:
        views.setdefault(str(row["view_id"]), row)
    chars = [
        float(row["prompt_chars"])
        for row in views.values()
        if row.get("prompt_chars") is not None
    ]
    return {
        "views": len(views),
        "prompt_chars": quantiles(chars),
        "length_finish_pct": pct(
            sum(row.get("finish_reason") == "length" for row in views.values()),
            len(views),
        ),
        "generation_failure_pct": pct(
            sum(bool(row.get("generation_failed")) for row in views.values()),
            len(views),
        ),
    }


def load_placeholder_case_ids(judge_root: Path) -> set[str]:
    result: set[str] = set()
    for path in (judge_root / "batches").glob("**/[!._]*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not value.get("metadata", {}).get("failure_scored_as_incorrect"):
            continue
        result.update(str(item["case_id"]) for item in value["judgments"])
    return result


def placeholder_sensitivity(
    rows: list[dict[str, Any]], placeholder_ids: set[str]
) -> dict[str, Any]:
    affected = sum(str(row.get("judge_case_id")) in placeholder_ids for row in rows)
    current = sum(bool(row["semantic"]) for row in rows)
    return {
        "affected_candidate_instances": affected,
        "lower_bound_asr_pct": pct(current, len(rows)),
        "all_affected_correct_upper_bound_asr_pct": pct(current + affected, len(rows)),
        "uncertainty_width_pp": pct(affected, len(rows)),
    }


def call_bin(value: int | None) -> str:
    if value is None or value < 0:
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


CALL_BIN_ORDER = ("0", "1-5", "6-10", "11-20", "21-40", "41+", "missing")


def catalog_metadata(root: Path) -> dict[str, dict[str, Any]]:
    output = {}
    for path in root.glob("S*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        metadata = value.get("metadata", {})
        output[str(metadata.get("scenario_id") or path.stem)] = metadata
    return output


def call_count_analysis(
    catalog: list[dict[str, Any]],
    schema: list[dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    schema_index = {
        (str(row.get("scenario_id")), str(row["attribute"])): row
        for row in schema
    }
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paired_schema: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in catalog:
        scenario = str(row.get("scenario_id"))
        value = metadata.get(scenario, {}).get("visible_call_count")
        label = call_bin(int(value) if value is not None else None)
        buckets[label].append(row)
        paired_schema[label].append(schema_index[(scenario, str(row["attribute"]))])
    output = {}
    for label in CALL_BIN_ORDER:
        if label not in buckets:
            continue
        left = summary(buckets[label])
        right = summary(paired_schema[label])
        output[label] = {
            "view_count": len({str(row["scenario_id"]) for row in buckets[label]}),
            "catalog": left,
            "paired_schema": right,
            "tool_call_lift_pp": round(left["asr_pct"] - right["asr_pct"], 3),
        }
    return output


def domain_attribute_lifts(
    catalog: list[dict[str, Any]], schema: list[dict[str, Any]]
) -> dict[str, Any]:
    output = {}
    for domain, catalog_rows in sorted(group_rows(catalog, "domain").items()):
        schema_rows = group_rows(schema, "domain")[domain]
        catalog_attrs = grouped_summary(catalog_rows, "attribute")
        schema_attrs = grouped_summary(schema_rows, "attribute")
        rows = []
        for attribute in sorted(catalog_attrs):
            rows.append(
                {
                    "attribute": attribute,
                    "catalog_asr_pct": catalog_attrs[attribute]["asr_pct"],
                    "schema_asr_pct": schema_attrs[attribute]["asr_pct"],
                    "lift_pp": round(
                        catalog_attrs[attribute]["asr_pct"]
                        - schema_attrs[attribute]["asr_pct"],
                        3,
                    ),
                }
            )
        output[domain] = sorted(rows, key=lambda row: row["lift_pp"], reverse=True)
    return output


def single_server_analysis(
    rows: list[dict[str, Any]],
    schema: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    schema_index = {
        (str(row.get("scenario_id")), str(row["attribute"])): row
        for row in schema
    }
    catalog_index = {
        (str(row.get("scenario_id")), str(row["attribute"])): row
        for row in catalog
    }

    def references(items: list[dict[str, Any]], index: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            index[(str(row.get("scenario_id")), str(row["attribute"]))]
            for row in items
        ]

    servers = []
    for name, items in sorted(group_rows(rows, "server_name").items()):
        own = summary(items)
        schema_ref = summary(references(items, schema_index))
        catalog_ref = summary(references(items, catalog_index))
        servers.append(
            {
                "server_name": name,
                "view_count": own["views"],
                "asr_pct": own["asr_pct"],
                "lift_vs_schema_pp": round(own["asr_pct"] - schema_ref["asr_pct"], 3),
                "gap_to_all_server_catalog_pp": round(catalog_ref["asr_pct"] - own["asr_pct"], 3),
            }
        )
    schema_reference_rows = references(rows, schema_index)
    catalog_reference_rows = references(rows, catalog_index)
    micro = summary(rows)
    schema_ref = summary(schema_reference_rows)
    catalog_ref = summary(catalog_reference_rows)
    return {
        "micro": micro,
        "paired_schema_micro": schema_ref,
        "paired_all_server_catalog_micro": catalog_ref,
        "lift_vs_schema_pp": round(micro["asr_pct"] - schema_ref["asr_pct"], 3),
        "gap_to_all_server_catalog_pp": round(catalog_ref["asr_pct"] - micro["asr_pct"], 3),
        "single_server_minus_schema_bootstrap": clustered_delta(
            rows,
            schema_reference_rows,
            samples=bootstrap_samples,
            seed=seed,
        ),
        "all_server_catalog_minus_single_server_bootstrap": clustered_delta(
            catalog_reference_rows,
            rows,
            samples=bootstrap_samples,
            seed=seed + 1,
        ),
        "per_domain": {
            domain: {
                "single_server": summary(items),
                "paired_schema": summary(references(items, schema_index)),
                "paired_all_server_catalog": summary(references(items, catalog_index)),
            }
            for domain, items in sorted(group_rows(rows, "domain").items())
        },
        "servers": servers,
    }


def build_markdown(analysis: dict[str, Any]) -> str:
    arms = analysis["arms"]
    lines = [
        "# Tool-call privacy leakage analysis",
        "",
        "This report uses fixed-denominator Semantic ASR. Deterministic matches are accepted first; unresolved open-ended values use the completed first-pass semantic judge. Uncertain and exhausted judge cases count as incorrect.",
        "",
        "## 1. Tool calls leak private attributes",
        "",
        "The paired schema-only arm exposes only profile field definitions and the output schema—no tool schemas and no executions. The catalog arm exposes the same scenario's observed tool metadata, calls, arguments, results, ordering, and server provenance. Their paired difference therefore estimates leakage attributable to the tool-call view under this experiment.",
        "",
        "| View | Semantic ASR | Correct / slots | Views | Generation failures |",
        "|---|---:|---:|---:|---:|",
    ]
    for arm in ARM_ORDER:
        row = arms[arm]
        lines.append(
            f"| {arm} | {row['asr_pct']:.3f}% | {row['correct']} / {row['slots']} | "
            f"{row['views']} | {row['generation_failed_views']} ({row['generation_failure_pct']:.3f}%) |"
        )
    lines.extend(
        [
            "",
            "### Paired ablations (profile-clustered bootstrap)",
            "",
            "| Ablation | Delta (pp) | 95% CI | Profiles |",
            "|---|---:|---:|---:|",
        ]
    )
    for name, row in analysis["ablations"].items():
        lines.append(
            f"| {name} | {row['delta_pp']:+.3f} | "
            f"[{row['ci95_pp'][0]:+.3f}, {row['ci95_pp'][1]:+.3f}] | {row['profiles']} |"
        )
    outcomes = analysis["tool_call_paired_outcomes"]
    lines.extend(
        [
            "",
            f"On {outcomes['slots']} paired slots, the catalog arm was correct while schema prior was wrong in **{outcomes.get('left_only', 0)}** cases; the reverse occurred in **{outcomes.get('right_only', 0)}** cases.",
            "",
            "Mechanistically, arguments can directly encode queries, locations, symptoms, purchases, or application intent; results can reveal diagnoses, products, jobs, routes, and content; call order and cross-server joins expose behavioral context even when no single field is a direct identifier.",
            "",
            "### Leakage by visible call count (descriptive, not randomized)",
            "",
            "| Visible calls | Views | Catalog ASR | Paired schema ASR | Lift |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, row in analysis["call_count_dose_response"].items():
        lines.append(
            f"| {label} | {row['view_count']} | {row['catalog']['asr_pct']:.3f}% | "
            f"{row['paired_schema']['asr_pct']:.3f}% | {row['tool_call_lift_pp']:+.3f} pp |"
        )

    lines.extend(
        [
            "",
            "## 2. View and domain leakage",
            "",
            "### Single-domain all-server views",
            "",
            "| Domain | Schema prior | Catalog calls | Raw calls | Two-stage final | Catalog lift (95% CI) |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    domains = analysis["domains"]
    for domain in sorted(domains):
        row = domains[domain]
        lift = row["catalog_one_shot"]["asr_pct"] - row["schema_prior"]["asr_pct"]
        interval = row["catalog_minus_schema_bootstrap"]["ci95_pp"]
        lines.append(
            f"| {domain} | {row['schema_prior']['asr_pct']:.3f}% | "
            f"{row['catalog_one_shot']['asr_pct']:.3f}% | {row['raw_one_shot']['asr_pct']:.3f}% | "
            f"{row['catalog_two_stage_final']['asr_pct']:.3f}% | {lift:+.3f} pp "
            f"[{interval[0]:+.3f}, {interval[1]:+.3f}] |"
        )
    lines.extend(["", "Top attribute lifts from tool calls within each domain:", ""])
    for domain, rows in analysis["domain_attribute_lifts"].items():
        top = ", ".join(f"{row['attribute']} ({row['lift_pp']:+.2f} pp)" for row in rows[:5])
        lines.append(f"- **{domain}:** {top}")

    server = analysis["single_server"]
    stable = [row for row in server["servers"] if row["view_count"] >= 100]
    stable.sort(key=lambda row: row["lift_vs_schema_pp"], reverse=True)
    lines.extend(
        [
            "",
            "### Server-isolated views",
            "",
            f"Across {server['micro']['views']} server views, micro Semantic ASR is **{server['micro']['asr_pct']:.3f}%**: "
            f"{server['lift_vs_schema_pp']:+.3f} pp vs paired schema prior and "
            f"{server['gap_to_all_server_catalog_pp']:+.3f} pp below paired all-server catalog views. "
            f"Under profile-balanced pairing, the corresponding deltas are "
            f"{server['single_server_minus_schema_bootstrap']['delta_pp']:+.3f} pp and "
            f"{server['all_server_catalog_minus_single_server_bootstrap']['delta_pp']:+.3f} pp, with 95% CIs "
            f"[{server['single_server_minus_schema_bootstrap']['ci95_pp'][0]:+.3f}, {server['single_server_minus_schema_bootstrap']['ci95_pp'][1]:+.3f}] and "
            f"[{server['all_server_catalog_minus_single_server_bootstrap']['ci95_pp'][0]:+.3f}, {server['all_server_catalog_minus_single_server_bootstrap']['ci95_pp'][1]:+.3f}], respectively.",
            "",
            "Servers with at least 100 views, ranked by lift over paired schema prior:",
            "",
            "| Server | Views | ASR | Lift vs schema | Gap to all-server |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in stable:
        lines.append(
            f"| {row['server_name']} | {row['view_count']} | {row['asr_pct']:.3f}% | "
            f"{row['lift_vs_schema_pp']:+.3f} | {row['gap_to_all_server_catalog_pp']:+.3f} |"
        )

    lines.extend(
        [
            "",
            "## 3. Ablation interpretation",
            "",
            "- **Executed calls vs schema prior** contrasts the complete observed tool-call view with a profile-schema-only prior.",
            "- **Raw repeated vs lossless catalog** tests serialization/repetition while retaining the same underlying trajectory; it is a representation ablation, not a no-information control.",
            "- **Two-stage final vs stage 1 / one-shot** tests whether a second inference pass recovers additional attributes.",
            "- **Cross-domain vs single-domain catalog** tests composition across four domains at profile level.",
            "- **Single-server vs all-server** tests view scope and shows that privacy risk composes across servers.",
            "",
            "## Validity and sensitivity",
            "",
            f"- The first-pass judge contains {analysis['judge_sensitivity']['placeholder_unique_cases']} exhausted unique cases materialized as incorrect. Even under the conservative worst-case sensitivity assignment, catalog tool calls retain at least **{analysis['judge_sensitivity']['comparison_bounds_pp']['catalog_minus_schema'][0]:+.3f} pp** lift over schema prior.",
            "- No second-pass verifier or adversarial adjudicator is present in this artifact set; these are first-pass Semantic ASR estimates and may over-accept some semantic matches.",
            "- Call-count strata are observational: more calls may correlate with harder or richer scenarios, so the dose-response table is descriptive rather than causal.",
            "- Each arm is a separate temperature-1.0 model generation. Profile bootstrap quantifies sampling variation across profiles, not rerun-to-rerun model stochasticity.",
            "- Single-server micro averages weight server views, while the cross-domain arm has one view per profile; compare paired deltas, not raw denominators alone.",
            "- Tool calls leak privacy here in the threat-model sense: an observer can infer hidden profile attributes above schema-only prior. This does not imply every individual call contains PII or that every inference is correct.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--judge-root", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    records = read_records(args.records.resolve())
    judgments, models = load_judgments(args.judge_root.resolve())
    diagnostics = attach_semantic_scores(records, judgments)
    placeholder_ids = load_placeholder_case_ids(args.judge_root.resolve())
    by_arm = group_rows(records, "arm")
    missing = set(ARM_ORDER) - set(by_arm)
    if missing:
        raise SystemExit(f"missing arms: {sorted(missing)}")

    arms = {arm: summary(by_arm[arm]) for arm in ARM_ORDER}
    comparisons = (
        ("tool_calls_catalog_minus_schema", "catalog_one_shot", "schema_prior"),
        ("raw_repeated_minus_catalog", "raw_one_shot", "catalog_one_shot"),
        ("two_stage_stage1_minus_one_shot", "catalog_two_stage_stage1", "catalog_one_shot"),
        ("two_stage_final_minus_stage1", "catalog_two_stage_final", "catalog_two_stage_stage1"),
        ("two_stage_final_minus_one_shot", "catalog_two_stage_final", "catalog_one_shot"),
        ("cross_domain_minus_single_domain_catalog", "cross_domain_all_servers", "catalog_one_shot"),
    )
    ablations = {
        name: clustered_delta(
            by_arm[left], by_arm[right], samples=args.bootstrap_samples, seed=args.seed + index
        )
        for index, (name, left, right) in enumerate(comparisons)
    }

    domain_names = sorted(
        name for name in group_rows(by_arm["catalog_one_shot"], "domain") if name != "None"
    )
    domains = {}
    domain_arms = (
        "schema_prior",
        "catalog_one_shot",
        "raw_one_shot",
        "catalog_two_stage_stage1",
        "catalog_two_stage_final",
    )
    for domain in domain_names:
        domains[domain] = {}
        for arm in domain_arms:
            domains[domain][arm] = summary(group_rows(by_arm[arm], "domain")[domain])
        domains[domain]["catalog_minus_schema_bootstrap"] = clustered_delta(
            group_rows(by_arm["catalog_one_shot"], "domain")[domain],
            group_rows(by_arm["schema_prior"], "domain")[domain],
            samples=args.bootstrap_samples,
            seed=args.seed + 100 + len(domains),
        )

    metadata = catalog_metadata(args.catalog_root.resolve())
    single_server = single_server_analysis(
        by_arm["single_server"],
        by_arm["schema_prior"],
        by_arm["catalog_one_shot"],
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed + 200,
    )
    sensitivities = {
        arm: placeholder_sensitivity(by_arm[arm], placeholder_ids) for arm in ARM_ORDER
    }
    def sensitivity_bounds(left: str, right: str) -> list[float]:
        left_value = sensitivities[left]
        right_value = sensitivities[right]
        return [
            round(
                left_value["lower_bound_asr_pct"]
                - right_value["all_affected_correct_upper_bound_asr_pct"],
                3,
            ),
            round(
                left_value["all_affected_correct_upper_bound_asr_pct"]
                - right_value["lower_bound_asr_pct"],
                3,
            ),
        ]
    analysis = {
        "schema_version": "1.0",
        "experiment": "tool_call_privacy_leakage_views_domains_ablations",
        "metric": "first_pass_semantic_asr",
        "record_count": len(records),
        "judge_models": sorted(models),
        "arms": arms,
        "ablations": ablations,
        "tool_call_paired_outcomes": paired_outcomes(
            by_arm["catalog_one_shot"], by_arm["schema_prior"]
        ),
        "domains": domains,
        "domain_attribute_lifts": domain_attribute_lifts(
            by_arm["catalog_one_shot"], by_arm["schema_prior"]
        ),
        "call_count_dose_response": call_count_analysis(
            by_arm["catalog_one_shot"], by_arm["schema_prior"], metadata
        ),
        "single_server": single_server,
        "request_diagnostics": {
            arm: request_diagnostics(by_arm[arm])
            for arm in (
                "schema_prior",
                "catalog_one_shot",
                "raw_one_shot",
                "catalog_two_stage_stage1",
                "catalog_two_stage_final",
                "cross_domain_all_servers",
            )
        },
        "judge_sensitivity": {
            "unique_cases": diagnostics["expected_unique_cases"],
            "unique_verdicts": diagnostics["unique_verdicts"],
            "weighted_verdicts": diagnostics["weighted_verdicts"],
            "source_counts": diagnostics["source_counts"],
            "placeholder_unique_cases": len(placeholder_ids),
            "by_arm": sensitivities,
            "comparison_bounds_pp": {
                "catalog_minus_schema": sensitivity_bounds("catalog_one_shot", "schema_prior"),
                "cross_domain_minus_catalog": sensitivity_bounds("cross_domain_all_servers", "catalog_one_shot"),
                "raw_minus_catalog": sensitivity_bounds("raw_one_shot", "catalog_one_shot"),
                "two_stage_final_minus_one_shot": sensitivity_bounds("catalog_two_stage_final", "catalog_one_shot"),
            },
        },
        "bootstrap": {
            "samples": args.bootstrap_samples,
            "seed": args.seed,
            "cluster": "profile_id",
        },
    }

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.markdown_output.write_text(build_markdown(analysis), encoding="utf-8")
    print(f"Analysis JSON: {args.json_output}")
    print(f"Report: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
