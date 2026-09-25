#!/usr/bin/env python3
"""Verify and optionally prepare the MCP catalog without rewriting source files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.json"
GENERATED_DIRS = {".git", ".venv", "node_modules", "build", "dist", "__pycache__"}


def run(command: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def tree_digest(component: Path) -> str:
    lines: list[bytes] = []
    for path in sorted(component.rglob("*")):
        if not path.is_file() or any(part in GENERATED_DIRS for part in path.parts):
            continue
        relative = path.relative_to(ROOT).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}\n".encode())
    return hashlib.sha256(b"".join(lines)).hexdigest()


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def check_component(name: str, metadata: dict[str, Any]) -> tuple[bool, str]:
    path = ROOT / name
    mode = metadata["mode"]
    if mode == "external-unlicensed":
        if not (path / ".git").is_dir():
            return True, "not redistributed (expected)"
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=path, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        ).stdout.strip()
        expected = metadata["revision"]
        return revision == expected, f"checkout {revision or 'unknown'}"
    if not path.is_dir():
        return False, "missing vendored component"
    expected = metadata.get("tree_sha256")
    actual = tree_digest(path)
    return actual == expected, f"tree {actual}"


def fetch_external(name: str, metadata: dict[str, Any]) -> None:
    target = ROOT / name
    if (target / ".git").is_dir():
        return
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(f"Refusing to overwrite non-empty {target}")
    target.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "--quiet"], cwd=target)
    run(["git", "remote", "add", "origin", metadata["source_url"]], cwd=target)
    run(["git", "fetch", "--quiet", "--depth", "1", "origin", metadata["revision"]], cwd=target)
    run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=target)


def install_dependencies(component: Path) -> None:
    if (component / "package-lock.json").is_file():
        if shutil.which("npm") is None:
            raise RuntimeError("npm is required for Node MCP components")
        run(["npm", "ci"], cwd=component)
        run(["npm", "run", "build", "--if-present"], cwd=component)
    elif (component / "package.json").is_file():
        if shutil.which("npm") is None:
            raise RuntimeError("npm is required for Node MCP components")
        run(["npm", "install", "--no-save", "--ignore-scripts"], cwd=component)
        run(["npm", "run", "build", "--if-present"], cwd=component)

    if (component / "pyproject.toml").is_file():
        if shutil.which("uv") is None:
            raise RuntimeError("uv is required for Python MCP components")
        run(["uv", "sync", "--no-dev"], cwd=component)
    elif (component / "requirements.txt").is_file():
        if shutil.which("uv") is None:
            raise RuntimeError("uv is required for Python MCP components")
        if not (component / ".venv").is_dir():
            run(["uv", "venv", ".venv"], cwd=component)
        run(
            ["uv", "pip", "install", "--python", ".venv/bin/python", "-r", "requirements.txt"],
            cwd=component,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify catalog provenance")
    parser.add_argument("--fetch-external", action="store_true", help="fetch omitted pinned sources")
    parser.add_argument("--accept-unlicensed-upstream", action="store_true")
    parser.add_argument("--install-dependencies", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest()
    components: dict[str, dict[str, Any]] = manifest["components"]

    if args.fetch_external and not args.accept_unlicensed_upstream:
        print(
            "External components have no explicit upstream license. Re-run with "
            "--accept-unlicensed-upstream after reviewing THIRD_PARTY_NOTICES.md.",
            file=sys.stderr,
        )
        return 2

    if args.fetch_external:
        for name, metadata in components.items():
            if metadata["mode"] == "external-unlicensed":
                fetch_external(name, metadata)

    failures = 0
    for name, metadata in components.items():
        ok, detail = check_component(name, metadata)
        print(f"{'OK' if ok else 'FAIL':4} {name}: {detail}")
        failures += int(not ok)

    if failures:
        return 1

    if args.install_dependencies:
        for name, metadata in components.items():
            path = ROOT / name
            if metadata["mode"] == "external-unlicensed" and not (path / ".git").is_dir():
                print(f"SKIP {name}: source was not fetched")
                continue
            install_dependencies(path)

    if not (args.check or args.fetch_external or args.install_dependencies):
        print("Catalog verified. Use --help for optional setup actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

