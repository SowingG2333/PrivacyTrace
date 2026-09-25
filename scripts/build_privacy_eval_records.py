#!/usr/bin/env python3
"""Normalize Privacy Attack V1 outputs into fixed-denominator evaluation rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES  # noqa: E402
from scripts.analyze_attack_comparison import normalized_match, strict_match  # noqa: E402


def parse_arm(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("arm must be NAME=PATH")
    name, path = value.split("=", 1)
    if not name or not path:
        raise argparse.ArgumentTypeError("arm must be NAME=PATH")
    return name, Path(path).resolve()


def profile_truth(path: Path) -> dict[str, dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {f"P{i:04d}": value for i, value in enumerate(values, start=1)}


def output_paths(root: Path, recursive: bool) -> list[Path]:
    paths = root.rglob("*.json") if recursive else root.glob("*.json")
    return sorted(
        path for path in paths
        if path.name not in {"_manifest.json", "_batch_summary.json"}
        and not path.name.startswith("._")
        and not any(part.startswith("_") for part in path.relative_to(root).parts)
    )


def rows_for_output(arm: str, path: Path, truths: dict[str, dict[str, Any]], *, stage: str | None = None) -> Iterable[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    metadata = value.get("metadata", {})
    profile_id = str(metadata.get("profile_id") or path.stem)
    if profile_id not in truths:
        raise ValueError(f"{path}: missing or unknown profile_id {profile_id!r}")
    scenario_id = metadata.get("scenario_id")
    domain = metadata.get("domain")
    server_name = metadata.get("server_name")
    view_id = str(profile_id if scenario_id is None else scenario_id)
    if server_name is not None:
        view_id = f"{view_id}::{server_name}"
    source = value.get(stage) if stage else value
    profile = source.get("profile") if isinstance(source, dict) else None
    if not isinstance(profile, dict):
        profile = {}
    generated_failed = bool(metadata.get("failure_scored_as_incorrect"))
    requests = metadata.get("requests") if isinstance(metadata.get("requests"), dict) else {}
    request_key = "stage1" if stage == "stage1" else ("one_shot" if "one_shot" in requests else "stage2")
    request = requests.get(request_key, {}) if isinstance(requests.get(request_key, {}), dict) else {}
    for attribute in PROFILE_ATTRIBUTES:
        item = profile.get(attribute)
        status = item.get("status") if isinstance(item, dict) else "failed"
        prediction = item.get("value") if isinstance(item, dict) else None
        failed = generated_failed or status == "failed"
        truth = truths[profile_id][attribute]
        yield {
            "arm": arm,
            "view_id": view_id,
            "scenario_id": scenario_id,
            "profile_id": profile_id,
            "domain": domain,
            "server_name": server_name,
            "attribute": attribute,
            "truth": truth,
            "prediction": prediction,
            "prediction_status": status,
            "prediction_source_stage": (
                item.get("source_stage") if isinstance(item, dict) else None
            ),
            "generation_failed": failed,
            "strict": False if failed else strict_match(attribute, prediction, truth),
            "normalized": False if failed else normalized_match(attribute, prediction, truth),
            "source_stage": stage,
            "model": metadata.get("model"),
            "attacker_observation_format": metadata.get("attacker_observation_format"),
            "prompt_chars": request.get("prompt_chars", metadata.get("observation_chars")),
            "prompt_tokens": (request.get("usage") or {}).get("prompt_tokens"),
            "finish_reason": request.get("finish_reason"),
            "failure_reason": metadata.get("failure_reason"),
        }


def add_arm(rows: list[dict[str, Any]], seen: set[tuple[str, str, str]], name: str, root: Path, truths: dict[str, dict[str, Any]], *, recursive: bool, two_stage: bool) -> None:
    if not root.is_dir():
        raise ValueError(f"arm directory does not exist: {root}")
    for path in output_paths(root, recursive):
        stages = ((f"{name}_stage1", "stage1"), (f"{name}_final", None)) if two_stage else ((name, None),)
        for arm, stage in stages:
            for row in rows_for_output(arm, path, truths, stage=stage):
                key = (arm, row["view_id"], row["attribute"])
                if key in seen:
                    raise ValueError(f"duplicate evaluation record key: {key}")
                seen.add(key); rows.append(row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--arm", action="append", default=[], type=parse_arm)
    parser.add_argument("--recursive-arm", action="append", default=[], type=parse_arm)
    parser.add_argument("--two-stage-arm", action="append", default=[], type=parse_arm)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    truths = profile_truth(args.profiles.resolve())
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for name, root in args.arm:
        add_arm(records, seen, name, root, truths, recursive=False, two_stage=False)
    for name, root in args.recursive_arm:
        add_arm(records, seen, name, root, truths, recursive=True, two_stage=False)
    for name, root in args.two_stage_arm:
        add_arm(records, seen, name, root, truths, recursive=False, two_stage=True)
    if not records:
        raise SystemExit("no records were produced")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    print(f"Wrote {len(records)} evaluation records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
