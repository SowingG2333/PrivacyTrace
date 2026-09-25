#!/usr/bin/env python3
"""Apply the current IPF targets to an existing completed candidate pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from privacy_trace.profile_generator import (  # noqa: E402
    GeneratorConfig,
    SyntheticProfileGenerator,
    check_profile,
    normalize_direct_identifier,
    load_targets,
    strip_internal_fields,
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            issues = check_profile(value)
            if issues:
                raise ValueError(
                    f"{path}:{line_number} is invalid: {', '.join(issues)}"
                )
            records.append(value)
    return records


def write_jsonl_atomic(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    temporary.replace(path)


def write_generation_report(
    path: Path | None,
    *,
    ipf_report: dict[str, Any],
    candidates: list[dict[str, Any]],
    args: argparse.Namespace,
    status: str,
    final_audit: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Persist calibration diagnostics even when sampling cannot proceed."""
    if path is None:
        return
    report = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    report["resampling_status"] = status
    report["ipf"] = ipf_report
    report["resampled_from_completed_candidate_pool"] = {
        "candidate_pool": str(args.candidate_pool.resolve()),
        "candidate_pool_size": len(candidates),
        "seed": args.seed,
        "sample_trials": args.sample_trials,
        "ipf_iterations_requested": args.ipf_iterations,
        "ipf_min_iterations": args.ipf_min_iterations,
        "ipf_tolerance": args.ipf_tolerance,
    }
    if final_audit is not None:
        report["final_audit"] = final_audit
    if error is not None:
        report["error"] = error
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--sample-trials", type=int, default=50)
    parser.add_argument("--ipf-iterations", type=int, default=500)
    parser.add_argument("--ipf-min-iterations", type=int, default=500)
    parser.add_argument("--ipf-tolerance", type=float, default=0.05)
    parser.add_argument(
        "--targets",
        type=Path,
        default=PROJECT_DIR / "data/targets/demographic_targets_2025.json",
        help="IPF target constraints used for the original candidate pool.",
    )
    parser.add_argument("--generation-report", type=Path)
    args = parser.parse_args()

    candidates = load_jsonl(args.candidate_pool)
    generator = SyntheticProfileGenerator(
        GeneratorConfig(
            count=args.count,
            candidate_count=len(candidates),
            seed=args.seed,
            sample_trials=args.sample_trials,
            ipf_iterations=args.ipf_iterations,
            ipf_min_iterations=args.ipf_min_iterations,
            ipf_tolerance=args.ipf_tolerance,
            require_full_skeleton_ipf=True,
        ),
        targets=load_targets(args.targets),
    )
    missing = generator.find_missing_target_cells(candidates)
    if missing:
        preview = ", ".join(
            f"{item['constraint']}:{item['key']}" for item in missing[:10]
        )
        raise RuntimeError(f"Candidate pool has unsupported target cells: {preview}")
    deficits = generator.find_target_capacity_deficits(candidates, args.count)
    if deficits:
        preview = ", ".join(
            f"{item['constraint']}:{item['key']}" for item in deficits[:10]
        )
        raise RuntimeError(f"Candidate pool has target capacity deficits: {preview}")

    ipf_report = generator.apply_ipf(candidates)
    if not ipf_report["converged"]:
        worst = max(ipf_report["final_l1"].items(), key=lambda item: item[1])
        error = f"IPF did not converge; worst constraint is {worst[0]} L1={worst[1]}"
        write_generation_report(
            args.generation_report,
            ipf_report=ipf_report,
            candidates=candidates,
            args=args,
            status="ipf_nonconverged",
            error=error,
        )
        raise RuntimeError(error)
    selection = generator.unique_selection_diagnostics(candidates, args.count)
    ipf_report["unique_selection"] = selection
    if selection["max_ideal_inclusion_probability"] > 1.0 + 1e-9:
        raise RuntimeError(
            "Calibrated weights cannot be represented by a unique sample: "
            f"max ideal inclusion probability={selection['max_ideal_inclusion_probability']}"
        )

    sampled = [
        strip_internal_fields(record)
        for record in generator.sample_profiles(candidates, args.count)
    ]
    invalid = [
        {"index": index, "issues": check_profile(record)}
        for index, record in enumerate(sampled)
        if check_profile(record)
    ]
    if invalid:
        raise RuntimeError(f"Resampled output is invalid: {invalid[:3]}")
    for field in ("email", "phone_number", "government_id"):
        identifiers = [
            normalize_direct_identifier(field, record[field])
            for record in sampled
        ]
        if len(identifiers) != len(set(identifiers)):
            raise RuntimeError(f"Resampled output contains duplicate {field}")

    write_jsonl_atomic(args.output, sampled)
    final_audit = generator.audit_final_sample(sampled)
    write_generation_report(
        args.generation_report,
        ipf_report=ipf_report,
        candidates=candidates,
        args=args,
        status="completed",
        final_audit=final_audit,
    )

    print(
        json.dumps(
            {
                "candidate_pool_size": len(candidates),
                "output_count": len(sampled),
                "ipf_iterations": ipf_report["iterations"],
                "worst_weighted_l1": max(ipf_report["final_l1"].values()),
                "effective_sample_size": ipf_report["weight_summary"][
                    "effective_sample_size"
                ],
                "max_ideal_inclusion_probability": selection[
                    "max_ideal_inclusion_probability"
                ],
                "final_sample_max_l1": final_audit["max_l1"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
