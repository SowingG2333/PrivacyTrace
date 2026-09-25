"""Streaming access to canonical or per-record trajectory artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any


CANONICAL_TRAJECTORY_FILENAME = "trajectories.jsonl"


@dataclass(frozen=True)
class TrajectoryRecordRef:
    """A lightweight locator for one trajectory without retaining it in memory."""

    source_path: Path
    scenario_id: str
    byte_offset: int | None
    byte_length: int
    sha256: str

    @property
    def name(self) -> str:
        return f"{self.scenario_id}.json"

    @property
    def stem(self) -> str:
        return self.scenario_id

    @property
    def display_path(self) -> str:
        source = Path(
            os.path.relpath(self.source_path.resolve(), Path.cwd().resolve())
        ).as_posix()
        if self.byte_offset is None:
            return source
        return f"{source}#{self.scenario_id}"

    def read(self) -> dict[str, Any]:
        if self.byte_offset is None:
            payload = self.source_path.read_bytes()
        else:
            with self.source_path.open("rb") as handle:
                handle.seek(self.byte_offset)
                payload = handle.read(self.byte_length)
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError(f"Trajectory is not a JSON object: {self.display_path}")
        actual_id = str(value.get("scenario_id") or "")
        if actual_id != self.scenario_id:
            raise ValueError(
                f"Trajectory identity changed at {self.display_path}: {actual_id!r}"
            )
        return value


def resolve_trajectory_input(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.is_dir():
        canonical = resolved / CANONICAL_TRAJECTORY_FILENAME
        if canonical.is_file():
            return canonical
    return resolved


def index_trajectories(
    input_path: Path,
    limit: int | None = None,
) -> list[TrajectoryRecordRef]:
    """Index trajectories from a canonical JSONL file or a legacy directory."""

    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    source = resolve_trajectory_input(input_path)
    if source.is_dir():
        refs = _index_json_files(source, limit)
    elif source.is_file() and source.suffix.casefold() == ".jsonl":
        refs = _index_jsonl(source, limit)
    else:
        raise ValueError(f"Trajectory input does not exist or is unsupported: {source}")
    if not refs:
        raise ValueError(f"No trajectories found in {source}")
    return refs


def _index_json_files(
    directory: Path,
    limit: int | None,
) -> list[TrajectoryRecordRef]:
    paths = sorted(directory.glob("S*.json"))
    if limit is not None:
        paths = paths[:limit]
    refs: list[TrajectoryRecordRef] = []
    for path in paths:
        payload = path.read_bytes()
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError(f"Trajectory is not a JSON object: {path}")
        scenario_id = str(value.get("scenario_id") or path.stem)
        refs.append(
            TrajectoryRecordRef(
                source_path=path.resolve(),
                scenario_id=scenario_id,
                byte_offset=None,
                byte_length=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    _validate_unique_ids(refs)
    return refs


def _index_jsonl(path: Path, limit: int | None) -> list[TrajectoryRecordRef]:
    refs: list[TrajectoryRecordRef] = []
    with path.open("rb") as handle:
        while limit is None or len(refs) < limit:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid trajectory JSONL record at byte {offset}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(
                    f"Trajectory JSONL record at byte {offset} is not an object"
                )
            scenario_id = str(value.get("scenario_id") or "")
            if not scenario_id:
                raise ValueError(
                    f"Trajectory JSONL record at byte {offset} has no scenario_id"
                )
            refs.append(
                TrajectoryRecordRef(
                    source_path=path.resolve(),
                    scenario_id=scenario_id,
                    byte_offset=offset,
                    byte_length=len(line),
                    sha256=hashlib.sha256(line.rstrip(b"\r\n")).hexdigest(),
                )
            )
    _validate_unique_ids(refs)
    return refs


def _validate_unique_ids(refs: list[TrajectoryRecordRef]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for ref in refs:
        if ref.scenario_id in seen:
            duplicates.append(ref.scenario_id)
        seen.add(ref.scenario_id)
    if duplicates:
        raise ValueError(
            "Duplicate trajectory scenario_id values: "
            + ", ".join(sorted(set(duplicates))[:20])
        )


def trajectory_input_files(input_path: Path) -> list[Path]:
    """Return physical files that define an input dataset for run contracts."""

    source = resolve_trajectory_input(input_path)
    if source.is_file():
        return [source]
    if source.is_dir():
        return sorted(source.glob("S*.json"))
    return []
