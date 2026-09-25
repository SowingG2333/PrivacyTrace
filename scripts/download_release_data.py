#!/usr/bin/env python3
"""Download, verify, and extract the ICLR 2027 review data assets."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "release/DATA_MANIFEST.json"
DEFAULT_ASSET_DIR = REPO_ROOT / "release_assets"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "PrivacyTrace-review-downloader/1"})
    with urllib.request.urlopen(request) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    temporary.replace(destination)


def safe_extract(archive_path: Path, output: Path) -> None:
    output_resolved = output.resolve()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            destination = (output / member.name).resolve()
            if destination != output_resolved and output_resolved not in destination.parents:
                raise ValueError(f"unsafe archive member: {member.name}")
        archive.extractall(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--asset-dir", type=Path, default=DEFAULT_ASSET_DIR)
    parser.add_argument(
        "--base-url",
        help="optional HTTP mirror used only when an asset is absent locally",
    )
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    output = args.output.resolve()
    asset_dir = args.asset_dir.resolve()
    downloads = output / ".downloads"
    downloads.mkdir(parents=True, exist_ok=True)

    local_assets: dict[str, Path] = {}
    for entry in manifest["archives"]:
        name = entry["name"]
        bundled = asset_dir / name
        if bundled.is_file() and sha256_file(bundled) == entry["sha256"]:
            path = bundled
        elif args.base_url:
            path = downloads / name
            if not path.is_file() or sha256_file(path) != entry["sha256"]:
                print(f"downloading {name} ({entry['bytes']} bytes)")
                download(f"{args.base_url.rstrip('/')}/{name}", path)
        else:
            raise SystemExit(
                f"Missing or unresolved Git LFS asset: {bundled}. "
                "Run 'git lfs pull' or provide --base-url."
            )
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            raise ValueError(f"SHA-256 mismatch for {name}: {actual}")
        local_assets[name] = path
        print(f"verified {name}")

    safe_extract(local_assets["privacytrace-profiles-scenarios.tar.gz"], output)
    safe_extract(local_assets["privacytrace-evaluations.tar.gz"], output)
    trajectory_output = output / "artifacts/trajectories/trajectories.jsonl"
    trajectory_output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(local_assets["privacytrace-trajectories.jsonl.gz"], "rb") as source:
        with trajectory_output.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)

    if args.verify:
        expected = int(manifest["dataset"]["trajectories"])
        with trajectory_output.open("rb") as handle:
            actual = sum(1 for line in handle if line.strip())
        if actual != expected:
            raise ValueError(f"trajectory count mismatch: expected {expected}, found {actual}")
        print(f"verified {actual} trajectories")

    print(f"release data extracted under {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
