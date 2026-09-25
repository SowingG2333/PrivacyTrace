#!/usr/bin/env python3
"""Analyze a generated PrivacyTrace candidate pool and its final IPF sample."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any, Iterable

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from privacy_trace.profile_generator import (
    EDUCATION_LEVELS,
    INCOME_LEVELS,
    age_group,
    age_stage,
    bucket_value,
    build_default_targets,
    check_profile,
    distribution_l1,
    normalize_direct_identifier,
    weighted_distribution,
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            records.append(value)
    return records


def rates(values: Iterable[str]) -> dict[str, float]:
    counts = Counter(values)
    total = sum(counts.values())
    return {
        key: count / total
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    }


def conditional_rates(
    records: list[dict[str, Any]],
    row_value,
    column_value,
    *,
    row_order: list[str] | None = None,
    column_order: list[str] | None = None,
) -> dict[str, dict[str, float]]:
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        table[str(row_value(record))][str(column_value(record))] += 1
    rows = row_order or sorted(table)
    output: dict[str, dict[str, float]] = {}
    for row in rows:
        counts = table.get(row, Counter())
        total = sum(counts.values())
        columns = column_order or sorted(counts)
        output[row] = {
            column: (counts.get(column, 0) / total if total else 0.0)
            for column in columns
        }
    return output


def condition_share_by(
    records: list[dict[str, Any]], condition_field: str, group_value
) -> dict[str, float]:
    totals: Counter[str] = Counter()
    positives: Counter[str] = Counter()
    for record in records:
        group = str(group_value(record))
        totals[group] += 1
        positives[group] += record[condition_field] != "None"
    return {
        group: positives[group] / totals[group]
        for group in sorted(totals)
    }


def mean_income_rank_by(
    records: list[dict[str, Any]], group_value, order: list[str]
) -> dict[str, float | None]:
    rank = {value: index for index, value in enumerate(INCOME_LEVELS)}
    values: dict[str, list[int]] = defaultdict(list)
    for record in records:
        values[str(group_value(record))].append(rank[str(record["income_level"])])
    return {
        group: (
            sum(values[group]) / len(values[group])
            if values.get(group)
            else None
        )
        for group in order
    }


def target_condition_share(
    targets: dict[str, float],
    *,
    group_index: int,
) -> dict[str, float]:
    totals: Counter[str] = Counter()
    positive: Counter[str] = Counter()
    for key, mass in targets.items():
        parts = key.split(" | ")
        group = parts[group_index]
        totals[group] += mass
        if parts[-1] != "None":
            positive[group] += mass
    return {
        group: positive[group] / totals[group]
        for group in sorted(totals)
    }


def target_conditional_table(
    targets: dict[str, float],
    *,
    row_index: int,
    column_index: int,
    row_order: list[str],
    column_order: list[str],
) -> dict[str, dict[str, float]]:
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for key, mass in targets.items():
        parts = key.split(" | ")
        table[parts[row_index]][parts[column_index]] += mass
    output: dict[str, dict[str, float]] = {}
    for row in row_order:
        total = sum(table[row].values())
        output[row] = {
            column: (table[row][column] / total if total else 0.0)
            for column in column_order
        }
    return output


def target_mean_income_rank(
    table: dict[str, dict[str, float]]
) -> dict[str, float]:
    rank = {value: index for index, value in enumerate(INCOME_LEVELS)}
    return {
        row: sum(rank[column] * share for column, share in columns.items())
        for row, columns in table.items()
    }


def direct_identifier_audit(
    records: list[dict[str, Any]]
) -> dict[str, dict[str, int | bool]]:
    result: dict[str, dict[str, int | bool]] = {}
    for field in ("email", "phone_number", "government_id"):
        normalized = [
            normalize_direct_identifier(field, record[field])
            for record in records
        ]
        unique = len(set(normalized))
        result[field] = {
            "count": len(normalized),
            "unique": unique,
            "duplicates": len(normalized) - unique,
            "all_unique": unique == len(normalized),
        }
    return result


def invalid_audit(
    records: list[dict[str, Any]], *, example_limit: int = 10
) -> dict[str, Any]:
    invalid_count = 0
    examples: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        issues = check_profile(record)
        if issues:
            invalid_count += 1
            if len(examples) < example_limit:
                examples.append({"index": index, "issues": issues})
    return {"invalid_count": invalid_count, "examples": examples}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--candidate-pool", type=Path, required=True)
    parser.add_argument("--generation-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    profiles = load_jsonl(args.profiles)
    candidates = load_jsonl(args.candidate_pool)
    constraints = build_default_targets()
    constraints_by_name = {constraint.name: constraint for constraint in constraints}

    weights = [1.0] * len(profiles)
    constraint_l1 = {}
    for constraint in constraints:
        current = weighted_distribution(profiles, weights, constraint.fields)
        constraint_l1[constraint.name] = distribution_l1(
            current, constraint.targets
        )

    physical_target = constraints_by_name[
        "current_country_x_age_group_x_sex_x_physical_condition"
    ].targets
    mental_target = constraints_by_name[
        "current_country_x_age_group_x_sex_x_mental_condition"
    ].targets
    occupation_target = constraints_by_name[
        "current_country_x_age_stage_x_sex_x_occupation_group"
    ].targets
    welfare_income_target = constraints_by_name[
        "current_country_x_welfare_education_group_x_income_level"
    ].targets

    welfare_order = ["no education", "primary", "secondary", "tertiary"]
    target_welfare_income = target_conditional_table(
        welfare_income_target,
        row_index=1,
        column_index=2,
        row_order=welfare_order,
        column_order=INCOME_LEVELS,
    )

    sample_welfare_income = conditional_rates(
        profiles,
        lambda record: bucket_value(record, "welfare_education_group"),
        lambda record: record["income_level"],
        row_order=welfare_order,
        column_order=INCOME_LEVELS,
    )

    occupation_target_totals: Counter[str] = Counter()
    occupation_target_none: Counter[str] = Counter()
    for key, mass in occupation_target.items():
        _, stage, _, occupation_group = key.split(" | ")
        occupation_target_totals[stage] += mass
        if occupation_group == "None":
            occupation_target_none[stage] += mass

    generation_report: dict[str, Any] = {}
    if args.generation_report and args.generation_report.exists():
        generation_report = json.loads(
            args.generation_report.read_text(encoding="utf-8")
        )

    age_groups = ["18-24", "25-34", "35-44", "45-54", "55-64", "65-80"]
    report = {
        "files": {
            "profiles": str(args.profiles.resolve()),
            "candidate_pool": str(args.candidate_pool.resolve()),
        },
        "sizes": {
            "candidate_pool": len(candidates),
            "final_profiles": len(profiles),
        },
        "validation": {
            "candidate_pool": invalid_audit(candidates),
            "final_profiles": invalid_audit(profiles),
            "candidate_pool_identifiers": direct_identifier_audit(candidates),
            "final_identifiers": direct_identifier_audit(profiles),
        },
        "marginals": {
            "country": rates(bucket_value(record, "current_country") for record in profiles),
            "age_group": rates(age_group(int(record["age"])) for record in profiles),
            "sex": rates(str(record["sex"]) for record in profiles),
            "education": rates(str(record["education_level"]) for record in profiles),
            "income": rates(str(record["income_level"]) for record in profiles),
            "relationship": rates(str(record["relationship_status"]) for record in profiles),
            "religion": rates(str(record["religious_belief"]) for record in profiles),
            "occupation_group": rates(bucket_value(record, "occupation_group") for record in profiles),
            "physical_condition": rates(str(record["physical_condition"]) for record in profiles),
            "mental_condition": rates(str(record["mental_condition"]) for record in profiles),
        },
        "joint_distributions": {
            "education_x_income": conditional_rates(
                profiles,
                lambda record: record["education_level"],
                lambda record: record["income_level"],
                row_order=EDUCATION_LEVELS,
                column_order=INCOME_LEVELS,
            ),
            "welfare_education_x_income": {
                "sample": sample_welfare_income,
                "target": target_welfare_income,
                "sample_mean_income_rank": target_mean_income_rank(sample_welfare_income),
                "target_mean_income_rank": target_mean_income_rank(target_welfare_income),
            },
            "mean_income_rank_by_age": mean_income_rank_by(
                profiles, lambda record: age_group(int(record["age"])), age_groups
            ),
            "relationship_by_age": conditional_rates(
                profiles,
                lambda record: age_group(int(record["age"])),
                lambda record: record["relationship_status"],
                row_order=age_groups,
            ),
            "not_employed_by_age_group": {
                group: sum(
                    record["occupation"] == "None"
                    for record in profiles
                    if age_group(int(record["age"])) == group
                )
                / max(
                    1,
                    sum(
                        age_group(int(record["age"])) == group
                        for record in profiles
                    ),
                )
                for group in age_groups
            },
            "not_employed_by_age_stage": {
                "sample": {
                    stage: sum(
                        record["occupation"] == "None"
                        for record in profiles
                        if age_stage(int(record["age"])) == stage
                    )
                    / max(
                        1,
                        sum(
                            age_stage(int(record["age"])) == stage
                            for record in profiles
                        ),
                    )
                    for stage in ("youth", "adult")
                },
                "target": {
                    stage: occupation_target_none[stage]
                    / occupation_target_totals[stage]
                    for stage in sorted(occupation_target_totals)
                },
            },
        },
        "health": {
            "physical_non_none": sum(
                record["physical_condition"] != "None" for record in profiles
            )
            / len(profiles),
            "mental_non_none": sum(
                record["mental_condition"] != "None" for record in profiles
            )
            / len(profiles),
            "physical_non_none_target": sum(
                mass
                for key, mass in physical_target.items()
                if key.split(" | ")[-1] != "None"
            ),
            "mental_non_none_target": sum(
                mass
                for key, mass in mental_target.items()
                if key.split(" | ")[-1] != "None"
            ),
            "physical_by_age": {
                "sample": condition_share_by(
                    profiles,
                    "physical_condition",
                    lambda record: age_group(int(record["age"])),
                ),
                "target": target_condition_share(
                    physical_target, group_index=1
                ),
            },
            "mental_by_age": {
                "sample": condition_share_by(
                    profiles,
                    "mental_condition",
                    lambda record: age_group(int(record["age"])),
                ),
                "target": target_condition_share(
                    mental_target, group_index=1
                ),
            },
            "mental_by_sex": {
                "sample": condition_share_by(
                    profiles, "mental_condition", lambda record: record["sex"]
                ),
                "target": target_condition_share(
                    mental_target, group_index=2
                ),
            },
        },
        "migration": {
            "birth_country_foreign": sum(
                bucket_value(record, "birth_migration_status") == "foreign"
                for record in profiles
            )
            / len(profiles),
            "citizenship_foreign": sum(
                bucket_value(record, "citizenship_migration_status") == "foreign"
                for record in profiles
            )
            / len(profiles),
        },
        "calibration": {
            "final_sample_constraint_l1": dict(
                sorted(constraint_l1.items(), key=lambda item: -item[1])
            ),
            "generation_ipf": generation_report.get("ipf"),
        },
        "llm": {
            "usage": generation_report.get("llm_usage"),
            "pii_validation": generation_report.get("pii_validation"),
            "api_key_count": generation_report.get("llm_api_key_count"),
            "effective_requests_per_minute": generation_report.get(
                "llm_effective_requests_per_minute"
            ),
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidate_pool": len(candidates),
                "final_profiles": len(profiles),
                "invalid_pool": report["validation"]["candidate_pool"]["invalid_count"],
                "invalid_final": report["validation"]["final_profiles"]["invalid_count"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
