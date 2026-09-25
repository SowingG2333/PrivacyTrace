#!/usr/bin/env python3
"""Analyze schema-only, server-view, and trajectory-channel privacy attacks."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES  # noqa: E402
from scripts.analyze_attack_comparison import (  # noqa: E402
    normalized_match,
    profile_for_id,
    strict_match,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_profiles(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_outputs(path: Path, *, recursive: bool = False) -> dict[str, dict[str, Any]]:
    paths = sorted(path.rglob("S*.json") if recursive else path.glob("S*.json"))
    if not paths:
        raise ValueError(f"No outputs found in {path}")
    outputs: dict[str, dict[str, Any]] = {}
    for item_path in paths:
        value = read_json(item_path)
        metadata = value.get("metadata", {})
        scenario_id = str(metadata.get("scenario_id") or item_path.stem)
        key = (
            f"{scenario_id}::{metadata.get('server_name')}"
            if recursive
            else scenario_id
        )
        if key in outputs:
            raise ValueError(f"Duplicate output key {key}: {item_path}")
        value["_path"] = str(item_path)
        outputs[key] = value
    return outputs


def prediction_records(
    label: str,
    outputs: Iterable[dict[str, Any]],
    profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for output in outputs:
        metadata = output.get("metadata", {})
        profile_id = metadata.get("profile_id")
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError(f"Missing profile_id in {output.get('_path')}")
        truth = profile_for_id(profiles, profile_id)
        prediction = output.get("profile")
        if not isinstance(prediction, dict) or set(prediction) != set(PROFILE_ATTRIBUTES):
            raise ValueError(f"Invalid profile in {output.get('_path')}")
        for attribute in PROFILE_ATTRIBUTES:
            value = prediction[attribute].get("value")
            records.append(
                {
                    "arm": label,
                    "scenario_id": metadata.get("scenario_id"),
                    "profile_id": profile_id,
                    "domain": metadata.get("domain"),
                    "server_name": metadata.get("server_name"),
                    "attribute": attribute,
                    "truth": truth[attribute],
                    "prediction": value,
                    "strict": strict_match(attribute, value, truth[attribute]),
                    "normalized": normalized_match(attribute, value, truth[attribute]),
                    "generation_failed": bool(
                        metadata.get("failure_scored_as_incorrect")
                    ),
                    "generation_failure_reason": metadata.get("failure_reason"),
                }
            )
    return records


def rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def pct(value: float) -> float:
    return round(100.0 * value, 3)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    strict_count = sum(bool(record["strict"]) for record in records)
    normalized_count = sum(bool(record["normalized"]) for record in records)
    return {
        "slots": total,
        "strict_correct": strict_count,
        "strict_rate": round(rate(strict_count, total), 6),
        "strict_pct": pct(rate(strict_count, total)),
        "normalized_correct": normalized_count,
        "normalized_rate": round(rate(normalized_count, total), 6),
        "normalized_pct": pct(rate(normalized_count, total)),
    }


def grouped_summary(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record.get(key))].append(record)
    return {name: summarize(items) for name, items in sorted(groups.items())}


def arm_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    scenarios = {record["scenario_id"] for record in records}
    profiles = {record["profile_id"] for record in records}
    return {
        "trajectory_count": len(scenarios),
        "profile_count": len(profiles),
        "overall": summarize(records),
        "per_attribute": grouped_summary(records, "attribute"),
        "per_domain": grouped_summary(records, "domain"),
    }


def delta_pp(left: dict[str, Any], right: dict[str, Any], metric: str) -> float:
    return round(left[f"{metric}_pct"] - right[f"{metric}_pct"], 3)


def paired_reference_records(
    records: list[dict[str, Any]],
    reference: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    paired = []
    for record in records:
        key = (str(record["scenario_id"]), str(record["attribute"]))
        if key not in reference:
            raise ValueError(f"Missing paired reference record: {key}")
        paired.append(reference[key])
    return paired


def paired_index(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(record["scenario_id"]), str(record["attribute"])): record
        for record in records
    }


def comparison(
    treatment: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, float]:
    return {
        "strict_lift_pp": delta_pp(treatment, reference, "strict"),
        "normalized_lift_pp": delta_pp(treatment, reference, "normalized"),
    }


def build_server_analysis(
    server_records: list[dict[str, Any]],
    schema_records: list[dict[str, Any]],
    full_records: list[dict[str, Any]],
) -> dict[str, Any]:
    schema_index = paired_index(schema_records)
    full_index = paired_index(full_records)
    by_server: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in server_records:
        by_server[str(record["server_name"])].append(record)

    servers: dict[str, Any] = {}
    for server_name, records in sorted(by_server.items()):
        schema_paired = paired_reference_records(records, schema_index)
        full_paired = paired_reference_records(records, full_index)
        view_overall = summarize(records)
        schema_overall = summarize(schema_paired)
        full_overall = summarize(full_paired)
        attributes = grouped_summary(records, "attribute")
        schema_attributes = grouped_summary(schema_paired, "attribute")
        full_attributes = grouped_summary(full_paired, "attribute")

        attribute_rows = []
        for attribute in PROFILE_ATTRIBUTES:
            item = attributes[attribute]
            schema_item = schema_attributes[attribute]
            full_item = full_attributes[attribute]
            attribute_rows.append(
                {
                    "attribute": attribute,
                    **item,
                    **comparison(item, schema_item),
                    "strict_gap_to_full_pp": delta_pp(full_item, item, "strict"),
                    "normalized_gap_to_full_pp": delta_pp(full_item, item, "normalized"),
                }
            )
        attribute_rows.sort(
            key=lambda item: (
                item["normalized_lift_pp"],
                item["normalized_pct"],
            ),
            reverse=True,
        )

        domain_rows: dict[str, Any] = {}
        by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            by_domain[str(record["domain"])].append(record)
        for domain, domain_records in sorted(by_domain.items()):
            schema_domain = paired_reference_records(domain_records, schema_index)
            full_domain = paired_reference_records(domain_records, full_index)
            item = summarize(domain_records)
            schema_item = summarize(schema_domain)
            full_item = summarize(full_domain)
            domain_rows[domain] = {
                **item,
                **comparison(item, schema_item),
                "strict_gap_to_full_pp": delta_pp(full_item, item, "strict"),
                "normalized_gap_to_full_pp": delta_pp(full_item, item, "normalized"),
            }

        servers[server_name] = {
            "view_count": len({record["scenario_id"] for record in records}),
            "overall": view_overall,
            "schema_reference": schema_overall,
            "full_reference": full_overall,
            **comparison(view_overall, schema_overall),
            "strict_gap_to_full_pp": delta_pp(full_overall, view_overall, "strict"),
            "normalized_gap_to_full_pp": delta_pp(
                full_overall, view_overall, "normalized"
            ),
            "attributes_ranked": attribute_rows,
            "per_domain": domain_rows,
        }

    schema_micro = paired_reference_records(server_records, schema_index)
    full_micro = paired_reference_records(server_records, full_index)
    server_micro = summarize(server_records)
    schema_micro_summary = summarize(schema_micro)
    full_micro_summary = summarize(full_micro)
    domain_micro: dict[str, Any] = {}
    by_domain_micro: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in server_records:
        by_domain_micro[str(record["domain"])].append(record)
    for domain, domain_records in sorted(by_domain_micro.items()):
        schema_domain = paired_reference_records(domain_records, schema_index)
        full_domain = paired_reference_records(domain_records, full_index)
        item = summarize(domain_records)
        schema_item = summarize(schema_domain)
        full_item = summarize(full_domain)
        domain_micro[domain] = {
            "view_count": len(domain_records) // len(PROFILE_ATTRIBUTES),
            **item,
            **comparison(item, schema_item),
            "strict_gap_to_full_pp": delta_pp(full_item, item, "strict"),
            "normalized_gap_to_full_pp": delta_pp(full_item, item, "normalized"),
        }
    return {
        "view_count": len(server_records) // len(PROFILE_ATTRIBUTES),
        "server_count": len(servers),
        "micro_overall": server_micro,
        "schema_reference_micro": schema_micro_summary,
        "full_reference_micro": full_micro_summary,
        **comparison(server_micro, schema_micro_summary),
        "strict_gap_to_full_pp": delta_pp(full_micro_summary, server_micro, "strict"),
        "normalized_gap_to_full_pp": delta_pp(
            full_micro_summary, server_micro, "normalized"
        ),
        "per_domain_micro": domain_micro,
        "servers": servers,
        "servers_ranked_by_normalized_lift": sorted(
            (
                {
                    "server_name": name,
                    "view_count": value["view_count"],
                    "normalized_pct": value["overall"]["normalized_pct"],
                    "normalized_lift_pp": value["normalized_lift_pp"],
                    "normalized_gap_to_full_pp": value["normalized_gap_to_full_pp"],
                }
                for name, value in servers.items()
            ),
            key=lambda item: (item["normalized_lift_pp"], item["view_count"]),
            reverse=True,
        ),
    }


def build_report(analysis: dict[str, Any]) -> str:
    arms = analysis["arms"]
    effects = analysis["channel_effects"]
    server = analysis["server_views"]
    lines = [
        "# Privacy channel decomposition",
        "",
        f"Model: `{analysis['model']}`",
        "",
        "## Overall ASR",
        "",
        "| Arm | Strict ASR | Normalized ASR |",
        "|---|---:|---:|",
    ]
    for label in (
        "schema_only",
        "metadata_sequence",
        "metadata_sequence_parameters",
        "metadata_sequence_results",
        "full_trajectory",
    ):
        overall = arms[label]["overall"]
        lines.append(
            f"| {label} | {overall['strict_pct']:.3f}% | "
            f"{overall['normalized_pct']:.3f}% |"
        )

    lines.extend(
        [
            "",
            "## Channel effects (percentage points)",
            "",
            "| Effect | Strict | Normalized |",
            "|---|---:|---:|",
        ]
    )
    for name, value in effects.items():
        lines.append(
            f"| {name} | {value['strict_pp']:+.3f} | "
            f"{value['normalized_pp']:+.3f} |"
        )

    lines.extend(
        [
            "",
            "## Server-view micro average",
            "",
            f"- Local views: {server['view_count']}",
            f"- Servers: {server['server_count']}",
            f"- Strict ASR: {server['micro_overall']['strict_pct']:.3f}% "
            f"(lift vs schema {server['strict_lift_pp']:+.3f} pp; "
            f"gap to full {server['strict_gap_to_full_pp']:+.3f} pp)",
            f"- Normalized ASR: {server['micro_overall']['normalized_pct']:.3f}% "
            f"(lift vs schema {server['normalized_lift_pp']:+.3f} pp; "
            f"gap to full {server['normalized_gap_to_full_pp']:+.3f} pp)",
            "",
            "## Servers ranked by normalized lift",
            "",
            "| Server | Views | ASR | Lift vs schema | Gap to full |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in server["servers_ranked_by_normalized_lift"]:
        lines.append(
            f"| {row['server_name']} | {row['view_count']} | "
            f"{row['normalized_pct']:.3f}% | {row['normalized_lift_pp']:+.3f} | "
            f"{row['normalized_gap_to_full_pp']:+.3f} |"
        )

    lines.extend(
        [
            "",
            "## Server-view leakage by domain",
            "",
            "| Domain | Views | ASR | Lift vs schema | Gap to full |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for domain, row in server["per_domain_micro"].items():
        lines.append(
            f"| {domain} | {row['view_count']} | {row['normalized_pct']:.3f}% | "
            f"{row['normalized_lift_pp']:+.3f} | "
            f"{row['normalized_gap_to_full_pp']:+.3f} |"
        )

    lines.extend(
        [
            "",
            "## Per-server most exposed attributes",
            "",
        ]
    )
    for server_name, value in sorted(server["servers"].items()):
        top = value["attributes_ranked"][:5]
        rendered = ", ".join(
            f"{item['attribute']} ({item['normalized_pct']:.1f}%, "
            f"lift {item['normalized_lift_pp']:+.1f} pp)"
            for item in top
        )
        lines.append(f"- **{server_name}** ({value['view_count']} views): {rendered}")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Schema-only estimates the model-prior baseline because its prompt contains only field definitions and the output contract.",
            "- Full minus schema-only is the attributable trajectory lift under this paired protocol, not proof of a specific causal mechanism for each individual prediction.",
            "- Parameter and result effects are paired arm differences over the same trajectories.",
            "- Interaction is `Full - Parameters - Results + Metadata/Sequence`; positive values indicate extra joint leakage beyond additive main effects.",
            "- Server-view micro averages weight each local server view equally; rare-server estimates should be interpreted with their view counts.",
            "",
        ]
    )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--schema-dir", type=Path, required=True)
    parser.add_argument("--metadata-sequence-dir", type=Path, required=True)
    parser.add_argument("--parameters-dir", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--server-view-dir", type=Path, required=True)
    parser.add_argument("--full-dir", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--records-output", type=Path)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    try:
        profiles = load_profiles(args.profiles.resolve())
        output_sets = {
            "schema_only": load_outputs(args.schema_dir.resolve()),
            "metadata_sequence": load_outputs(args.metadata_sequence_dir.resolve()),
            "metadata_sequence_parameters": load_outputs(args.parameters_dir.resolve()),
            "metadata_sequence_results": load_outputs(args.results_dir.resolve()),
            "full_trajectory": load_outputs(args.full_dir.resolve()),
        }
        scenario_sets = {name: set(values) for name, values in output_sets.items()}
        reference_scenarios = scenario_sets["schema_only"]
        for name in (
            "metadata_sequence",
            "metadata_sequence_parameters",
            "metadata_sequence_results",
        ):
            scenarios = scenario_sets[name]
            if scenarios != reference_scenarios:
                raise ValueError(
                    f"Scenario coverage mismatch for {name}: "
                    f"missing={len(reference_scenarios - scenarios)} "
                    f"extra={len(scenarios - reference_scenarios)}"
                )
        missing_full = reference_scenarios - scenario_sets["full_trajectory"]
        if missing_full:
            raise ValueError(
                f"Full-trajectory reference is missing {len(missing_full)} scenarios"
            )
        output_sets["full_trajectory"] = {
            scenario_id: output_sets["full_trajectory"][scenario_id]
            for scenario_id in sorted(reference_scenarios)
        }

        records_by_arm = {
            name: prediction_records(name, values.values(), profiles)
            for name, values in output_sets.items()
        }
        server_outputs = load_outputs(args.server_view_dir.resolve(), recursive=True)
        server_records = prediction_records(
            "server_view", server_outputs.values(), profiles
        )
        arms = {name: arm_summary(records) for name, records in records_by_arm.items()}

        schema = arms["schema_only"]["overall"]
        metadata = arms["metadata_sequence"]["overall"]
        parameters = arms["metadata_sequence_parameters"]["overall"]
        results = arms["metadata_sequence_results"]["overall"]
        full = arms["full_trajectory"]["overall"]

        def effect(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
            return {
                "strict_pp": delta_pp(left, right, "strict"),
                "normalized_pp": delta_pp(left, right, "normalized"),
            }

        interaction = {
            metric: round(
                full[f"{metric}_pct"]
                - parameters[f"{metric}_pct"]
                - results[f"{metric}_pct"]
                + metadata[f"{metric}_pct"],
                3,
            )
            for metric in ("strict", "normalized")
        }
        analysis = {
            "schema_version": "1.0",
            "experiment": "privacy_channel_decomposition",
            "model": next(iter(output_sets["schema_only"].values()))
            .get("metadata", {})
            .get("model"),
            "trajectory_count": len(reference_scenarios),
            "profile_count": len(profiles),
            "attribute_count": len(PROFILE_ATTRIBUTES),
            "arms": arms,
            "channel_effects": {
                "attributable_trajectory_leakage_full_minus_schema": effect(full, schema),
                "metadata_sequence_leakage_vs_schema": effect(metadata, schema),
                "parameter_leakage": effect(parameters, metadata),
                "result_leakage": effect(results, metadata),
                "parameter_result_interaction": {
                    "strict_pp": interaction["strict"],
                    "normalized_pp": interaction["normalized"],
                },
            },
            "server_views": build_server_analysis(
                server_records,
                records_by_arm["schema_only"],
                records_by_arm["full_trajectory"],
            ),
            "notes": [
                "All ASRs use all 17 attribute slots as the fixed denominator.",
                "Every generated arm contains one independently requested completion per trajectory or server view.",
                "Full-trajectory outputs are reused from the existing one-shot run.",
                "Server-view call order is renumbered locally after removing every other server call.",
            ],
        }
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.markdown_output.write_text(build_report(analysis), encoding="utf-8")
    if args.records_output is not None:
        args.records_output.parent.mkdir(parents=True, exist_ok=True)
        with args.records_output.open("w", encoding="utf-8") as handle:
            for records in records_by_arm.values():
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            for record in server_records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Analysis JSON: {args.json_output}")
    print(f"Analysis report: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
