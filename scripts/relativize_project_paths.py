#!/usr/bin/env python3
"""Replace a checkout-specific absolute prefix with portable relative paths."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


SKIP_DIRS = {
    ".git",
    ".venv",
    ".cache",
    ".uv-cache",
    ".uv-python",
    "__pycache__",
    "migration",
}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--root", type=Path, default=Path.cwd())
    value.add_argument("--old-root", type=Path, required=True)
    value.add_argument("--dry-run", action="store_true")
    return value


def candidate_files(root: Path):
    for directory, names, filenames in os.walk(root):
        names[:] = [name for name in names if name not in SKIP_DIRS]
        base = Path(directory)
        for filename in filenames:
            path = base / filename
            if path.is_file() and not path.is_symlink():
                yield path


def main() -> int:
    args = parser().parse_args()
    root = args.root.resolve()
    old_root = str(args.old_root.resolve()).encode()
    prefix = old_root + b"/"
    changed = 0
    replacements = 0
    bytes_removed = 0
    for path in candidate_files(root):
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        count = payload.count(prefix)
        exact_count = payload.count(old_root) - count
        if count == 0 and exact_count == 0:
            continue
        rewritten = payload.replace(prefix, b"").replace(old_root, b".")
        if rewritten == payload:
            continue
        changed += 1
        replacements += count + max(0, exact_count)
        bytes_removed += len(payload) - len(rewritten)
        if args.dry_run:
            continue
        temporary = path.with_name(f".{path.name}.relativize.tmp")
        temporary.write_bytes(rewritten)
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    print(
        f"changed_files={changed} replacements={replacements} "
        f"bytes_removed={bytes_removed} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
