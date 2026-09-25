"""Crash-safe agent trajectory persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_env.recovery import atomic_write_json


def write_trajectory(path: Path, value: dict[str, Any]) -> None:
    atomic_write_json(path, value)
