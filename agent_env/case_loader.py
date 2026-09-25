"""Load profile/scenario records into a single task boundary object."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_env.models import TaskCase


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number} of {path}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Line {line_number} of {path} is not an object")
            records.append(value)
    return records


def load_task_case(
    profiles_path: Path, scenarios_path: Path, scenario_id: str
) -> TaskCase:
    matches = [
        item for item in read_jsonl(scenarios_path)
        if item.get("scenario_id") == scenario_id
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one scenario with id {scenario_id}, found {len(matches)}")
    scenario_record = matches[0]
    profile_id = scenario_record.get("profile_id")
    user_task = scenario_record.get("user_task")
    domain = scenario_record.get("domain")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (profile_id, domain)
    ):
        raise ValueError(f"Scenario {scenario_id} is missing profile_id or domain")
    required_task_fields = {"goal", "context", "constraints", "expected_result"}
    if not isinstance(user_task, dict) or set(user_task) != required_task_fields:
        raise ValueError(f"Scenario {scenario_id}.user_task has an invalid schema")
    if not all(
        isinstance(user_task.get(field), str) and user_task[field].strip()
        for field in ("goal", "context", "expected_result")
    ) or not isinstance(user_task.get("constraints"), list) or not all(
        isinstance(item, str) and item.strip()
        for item in user_task.get("constraints", [])
    ):
        raise ValueError(f"Scenario {scenario_id}.user_task has invalid values")

    profile: dict[str, Any] | None = None
    for index, value in enumerate(read_jsonl(profiles_path), start=1):
        derived_id = str(value.get("profile_id") or f"P{index:04d}")
        if derived_id == profile_id:
            profile = value
            break
    if profile is None:
        raise ValueError(f"No profile found for {profile_id}")

    criteria = scenario_record.get("evaluation_criteria", [])
    if isinstance(criteria, str):
        criteria = [criteria]
    if not isinstance(criteria, list) or not all(isinstance(item, str) for item in criteria):
        raise ValueError(f"Scenario {scenario_id}.evaluation_criteria must be a string list")
    known = {
        "scenario_id", "profile_id", "domain", "user_task",
        "evaluation_criteria",
        # Tool planning is scenario-synthesis provenance, not runtime task
        # state. Legacy cases may still carry this field, but neither the
        # simulator nor the evaluated agent may receive it.
        "selected_tools", "tool_roles",
    }
    return TaskCase(
        scenario_id=scenario_id,
        profile_id=profile_id,
        domain=domain,
        user_task=dict(user_task),
        profile=profile,
        evaluation_criteria=tuple(criteria),
        metadata={key: value for key, value in scenario_record.items() if key not in known},
    )
