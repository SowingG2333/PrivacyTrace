#!/usr/bin/env python3
"""Build deterministic, anonymized GitHub Release data assets."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_MTIME = 1_790_294_400  # 2026-09-25 00:00:00 UTC
TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".txt"}
EXCLUDED_PARTS = {
    "_recovery",
    "_smoke",
    "_smoke_key_pool",
    "_smoke_key_pool_v2",
    "_smoke_no_proxy",
    "logs",
    "monitor",
    "__pycache__",
}
EXCLUDED_NAME_FRAGMENTS = ("_legacy", "all_keys_invalid", "model_attack_smoke")
EVALUATION_RUNS = (
    "deepseek_v4_flash_0731",
    "gpt_5_5",
    "minimax_m3",
    "gemini_3_flash_preview",
    "model_generalization_boyue_glm52",
)
AUTOMATIC_REDACTIONS = (
    (
        "openai_style_secret",
        re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b"),
        "[REDACTED_SECRET]",
    ),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{32,}\b"), "[REDACTED_SECRET]"),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), "[REDACTED_SECRET]"),
    (
        "duffel_token",
        re.compile(r"\bduffel_(?:test|live)_[A-Za-z0-9_-]{20,}\b"),
        "[REDACTED_SECRET]",
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}"),
        "Bearer [REDACTED_SECRET]",
    ),
    (
        "local_home_path",
        re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/"),
        "/home/[REDACTED_USER]/",
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def include_path(path: Path) -> bool:
    if path.name.startswith("._") or path.name in {".DS_Store"}:
        return False
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return False
    return not any(fragment in part for part in path.parts for fragment in EXCLUDED_NAME_FRAGMENTS)


def copy_tree_filtered(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not include_path(relative):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def load_redactions(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise ValueError("redactions file must contain an entries list")
    for entry in entries:
        if set(entry) != {"label", "find", "replace"} or not entry["find"]:
            raise ValueError("each redaction needs non-empty label/find/replace strings")
    return entries


def apply_redactions(root: Path, entries: list[dict[str, str]]) -> dict[str, int]:
    counts = {entry["label"]: 0 for entry in entries}
    counts.update({label: 0 for label, _, _ in AUTOMATIC_REDACTIONS})
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        temporary = path.with_name(path.name + ".redacting")
        changed = False
        with path.open(encoding="utf-8") as source, temporary.open("w", encoding="utf-8") as destination:
            for value in source:
                updated = value
                for entry in entries:
                    occurrences = updated.count(entry["find"])
                    if occurrences:
                        counts[entry["label"]] += occurrences
                        updated = updated.replace(entry["find"], entry["replace"])
                        changed = True
                for label, pattern, replacement in AUTOMATIC_REDACTIONS:
                    updated, occurrences = pattern.subn(replacement, updated)
                    if occurrences:
                        counts[label] += occurrences
                        changed = True
                destination.write(updated)
        if changed:
            temporary.replace(path)
        else:
            temporary.unlink()
    return counts


def normalized_tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = ARCHIVE_MTIME
    if info.isfile():
        info.mode = 0o644
    elif info.isdir():
        info.mode = 0o755
    return info


def write_deterministic_tar_gz(output: Path, roots: Iterable[tuple[Path, str]]) -> None:
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for source, archive_name in roots:
                    archive.add(source, arcname=archive_name, recursive=True, filter=normalized_tarinfo)


def write_deterministic_gzip(source: Path, output: Path) -> None:
    with source.open("rb") as input_handle, output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as compressed:
            shutil.copyfileobj(input_handle, compressed, length=1024 * 1024)


def file_stats(root: Path) -> tuple[int, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return len(files), sum(path.stat().st_size for path in files)


def read_csv_records(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_staging(source_root: Path, staging: Path) -> None:
    artifacts = staging / "artifacts"
    for name in ("profile_pool", "scenario"):
        copy_tree_filtered(source_root / "artifacts" / name, artifacts / name)

    trajectory_source = source_root / "artifacts/trajectories"
    for name in ("trajectories.jsonl", "combined_audit.json", "_batch_summary.json"):
        copy_file(trajectory_source / name, artifacts / "trajectories" / name)

    experiment_source = source_root / "artifacts/privacy_experiments"
    for run in EVALUATION_RUNS:
        copy_tree_filtered(experiment_source / run, artifacts / "privacy_experiments" / run)

    reproducibility = artifacts / "reproducibility"
    for name in ("model_versions_and_execution_dates.csv", "recorded_token_usage_and_cost.csv"):
        copy_file(source_root / "artifacts/repro_audit" / name, reproducibility / name)


def build_manifest(
    staging: Path,
    output_dir: Path,
    redaction_counts: dict[str, int],
) -> dict[str, Any]:
    profile_report = json.loads(
        (staging / "artifacts/profile_pool/generation_report.json").read_text(encoding="utf-8")
    )
    scenario_audit = json.loads(
        (staging / "artifacts/scenario/combined_audit.json").read_text(encoding="utf-8")
    )
    trajectory_audit = json.loads(
        (staging / "artifacts/trajectories/combined_audit.json").read_text(encoding="utf-8")
    )
    archives = []
    for name in (
        "privacytrace-profiles-scenarios.tar.gz",
        "privacytrace-trajectories.jsonl.gz",
        "privacytrace-evaluations.tar.gz",
    ):
        path = output_dir / name
        archives.append(
            {"name": name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    public_redaction_counts = {
        "identity_name_matches": sum(
            count for label, count in redaction_counts.items() if label.startswith("author_name")
        ),
        "identity_account_matches": sum(
            count
            for label, count in redaction_counts.items()
            if label.startswith("author_handle") or label == "personal_github"
        ),
        "affiliation_or_endpoint_matches": sum(
            count
            for label, count in redaction_counts.items()
            if label.startswith("affiliation") or label == "institution_domain"
        ),
        "source_workspace_matches": redaction_counts.get("source_workspace", 0),
        "secret_or_token_matches": sum(
            redaction_counts.get(label, 0)
            for label in (
                "openai_style_secret", "google_api_key", "huggingface_token",
                "duffel_token", "bearer_token",
            )
        ),
        "local_home_path_matches": redaction_counts.get("local_home_path", 0),
    }
    return {
        "schema_version": 1,
        "release": "iclr2027-v1",
        "terms": "review-only; see REVIEW_LICENSE.md and DATA_CARD.md",
        "archives": archives,
        "dataset": {
            "profiles": profile_report["requested_count"],
            "profile_attributes": 17,
            "scenarios": scenario_audit["record_count"],
            "domains": scenario_audit["domain_counts"],
            "trajectories": trajectory_audit["record_count"],
            "tool_records": trajectory_audit["tool_record_count"],
            "mcp_executions": trajectory_audit["mcp_executed_record_count"],
            "cache_hits": trajectory_audit["cache_hit_record_count"],
        },
        "models_and_execution_dates": read_csv_records(
            staging / "artifacts/reproducibility/model_versions_and_execution_dates.csv"
        ),
        "redactions": public_redaction_counts,
        "excluded": [
            "manuscript source and author metadata",
            "credentials and internal endpoint configuration",
            "logs, recovery checkpoints, monitors, and smoke runs",
            "legacy or failed judge variants",
            "experiments not used by the reported paper tables",
            "macOS AppleDouble files and generated caches",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, default=REPO_ROOT / "release")
    parser.add_argument("--redactions-file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    manifest_output = args.manifest_output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_output.mkdir(parents=True, exist_ok=True)
    redactions = load_redactions(args.redactions_file)

    with tempfile.TemporaryDirectory(prefix="privacytrace-release-", dir=output_dir) as temporary:
        staging = Path(temporary)
        build_staging(source_root, staging)
        redaction_counts = apply_redactions(staging, redactions)

        write_deterministic_tar_gz(
            output_dir / "privacytrace-profiles-scenarios.tar.gz",
            (
                (staging / "artifacts/profile_pool", "artifacts/profile_pool"),
                (staging / "artifacts/scenario", "artifacts/scenario"),
                (staging / "artifacts/trajectories/combined_audit.json", "artifacts/trajectories/combined_audit.json"),
                (staging / "artifacts/trajectories/_batch_summary.json", "artifacts/trajectories/_batch_summary.json"),
                (staging / "artifacts/reproducibility", "artifacts/reproducibility"),
            ),
        )
        write_deterministic_gzip(
            staging / "artifacts/trajectories/trajectories.jsonl",
            output_dir / "privacytrace-trajectories.jsonl.gz",
        )
        write_deterministic_tar_gz(
            output_dir / "privacytrace-evaluations.tar.gz",
            ((staging / "artifacts/privacy_experiments", "artifacts/privacy_experiments"),),
        )

        manifest = build_manifest(staging, output_dir, redaction_counts)

    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    (output_dir / "DATA_MANIFEST.json").write_text(manifest_text, encoding="utf-8")
    (manifest_output / "DATA_MANIFEST.json").write_text(manifest_text, encoding="utf-8")
    checksum_text = "".join(
        f"{entry['sha256']}  {entry['name']}\n" for entry in manifest["archives"]
    )
    (output_dir / "SHA256SUMS").write_text(checksum_text, encoding="utf-8")
    (manifest_output / "SHA256SUMS").write_text(checksum_text, encoding="utf-8")

    file_count, uncompressed_bytes = file_stats(source_root / "artifacts")
    print(f"release assets written to {output_dir}")
    print(f"source artifact inventory (before filtering): {file_count} files, {uncompressed_bytes} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
