#!/usr/bin/env python3
"""Fail-closed validation for the anonymous repository and extracted data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable


APPLEDOUBLE = re.compile(r"^\._")
SECRET_PATTERNS = {
    "OpenAI-style secret": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{32,}\b"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    "Duffel token": re.compile(r"\bduffel_(?:test|live)_[A-Za-z0-9_-]{20,}\b"),
    "Bearer token": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
INFRASTRUCTURE_PATTERNS = {
    "institutional model endpoint": re.compile(r"api\.pjlab\.org\.cn", re.I),
    "source workspace path": re.compile(r"/data/agent_privacy(?:/|\b)"),
    "user home path": re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/"),
}
SKIP_DIRS = {
    ".downloads", ".git", ".venv", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache",
}
TEXT_SUFFIXES = {
    ".cfg", ".csv", ".env", ".ini", ".js", ".json", ".jsonl", ".lock",
    ".md", ".mjs", ".py", ".sh", ".tex", ".toml", ".ts", ".txt", ".yaml", ".yml",
}


def iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


def load_private_patterns(path: Path | None) -> list[tuple[str, str]]:
    if path is None:
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return [
        (entry["label"], entry["find"].casefold())
        for entry in value["entries"]
    ]


def scan_tree(root: Path, private_patterns: list[tuple[str, str]], *, repo: bool) -> list[str]:
    failures: list[str] = []
    text_files: list[Path] = []
    for path in iter_files(root):
        relative = path.relative_to(root)
        if APPLEDOUBLE.match(path.name):
            failures.append(f"AppleDouble file: {relative}")
        if path.name in {".env", ".env.mcp", "api_key"}:
            failures.append(f"private configuration file: {relative}")
        lfs_review_asset = relative.parts[:1] == ("release_assets",)
        if repo and path.stat().st_size > 95 * 1024 * 1024 and not lfs_review_asset:
            failures.append(f"Git-hostile file over 95 MiB: {relative}")
        if path.resolve() == Path(__file__).resolve():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"Dockerfile", "Makefile", ".env.example"}:
            text_files.append(path)

    rg = shutil.which("rg")
    files_to_scan = text_files
    if rg:
        combined = "(?:" + "|".join(
            [
                *(pattern.pattern for pattern in SECRET_PATTERNS.values()),
                *(pattern.pattern for pattern in INFRASTRUCTURE_PATTERNS.values()),
                *(re.escape(literal) for _, literal in private_patterns),
            ]
        ) + ")"
        result = subprocess.run(
            [
                rg, "--files-with-matches", "--hidden", "--no-ignore", "--ignore-case",
                "--glob", "!scripts/validate_release.py",
                "--glob", "!**/.downloads/**",
                "--glob", "!**/.git/**",
                "--glob", "!**/.venv/**",
                "--glob", "!**/node_modules/**",
                "--glob", "!release_assets/**",
                combined, str(root),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode not in {0, 1}:
            failures.append(f"ripgrep scan failed: {result.stderr.strip()}")
            return failures
        files_to_scan = [Path(line) for line in result.stdout.splitlines() if line.strip()]

    for path in files_to_scan:
        relative = path.relative_to(root)
        pending_patterns = dict([*SECRET_PATTERNS.items(), *INFRASTRUCTURE_PATTERNS.items()])
        pending_literals = dict(private_patterns)
        try:
            with path.open(encoding="utf-8") as handle:
                for line_number, text in enumerate(handle, start=1):
                    for label, pattern in list(pending_patterns.items()):
                        if pattern.search(text):
                            failures.append(f"{label}: {relative}:{line_number}")
                            pending_patterns.pop(label)
                    if pending_literals:
                        folded = text.casefold()
                        for label, literal in list(pending_literals.items()):
                            if literal in folded:
                                failures.append(f"{label}: {relative}:{line_number}")
                                pending_literals.pop(label)
                    if not pending_patterns and not pending_literals:
                        break
        except UnicodeDecodeError:
            continue
    return failures


def count_jsonl(path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            json.loads(line)
            count += 1
    return count


def validate_data(root: Path) -> list[str]:
    failures: list[str] = []
    manifest = json.loads((Path(__file__).resolve().parents[1] / "release/DATA_MANIFEST.json").read_text())
    expected = manifest["dataset"]
    paths = {
        "profiles": root / "artifacts/profile_pool/profiles.jsonl",
        "scenarios": root / "artifacts/scenario/cases.jsonl",
        "trajectories": root / "artifacts/trajectories/trajectories.jsonl",
    }
    for label, path in paths.items():
        if not path.is_file():
            failures.append(f"missing data file: {path}")
            continue
        actual = count_jsonl(path)
        if actual != int(expected[label]):
            failures.append(f"{label} count: expected {expected[label]}, found {actual}")
    audit_path = root / "artifacts/trajectories/combined_audit.json"
    if audit_path.is_file():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("tool_record_count") != expected["tool_records"]:
            failures.append("tool-record count does not match DATA_MANIFEST.json")
    return failures


def validate_git(repo: Path, private_patterns: list[tuple[str, str]]) -> list[str]:
    if not (repo / ".git").is_dir():
        return []
    failures: list[str] = []
    log = subprocess.run(
        ["git", "log", "--format=%an%n%ae%n%B"], cwd=repo, text=True,
        stdout=subprocess.PIPE, check=True,
    ).stdout
    folded_log = log.casefold()
    for label, literal in private_patterns:
        if literal in folded_log:
            failures.append(f"{label}: Git history")
    remotes = subprocess.run(
        ["git", "remote", "-v"], cwd=repo, text=True,
        stdout=subprocess.PIPE, check=True,
    ).stdout.strip()
    if remotes:
        failures.append("Git remote is configured before anonymous publication audit")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--identity-patterns", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    private_patterns = load_private_patterns(args.identity_patterns)
    failures = scan_tree(repo, private_patterns, repo=True)
    failures.extend(validate_git(repo, private_patterns))
    if args.data_root:
        data_root = args.data_root.resolve()
        failures.extend(scan_tree(data_root, private_patterns, repo=False))
        failures.extend(validate_data(data_root))
    if failures:
        print("anonymous release validation: FAIL", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("anonymous release validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
