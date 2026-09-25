"""Generate profile-conditioned, tool-grounded user tasks.

The production path has one LLM stage: a profile-conditioned planner accepts or
rejects one deterministic random candidate-tool subset and, on acceptance,
emits a structured user task plus a complete hidden tool plan. Planner output is
then checked only against the published structural contract. Tool
calls are allowed to fail or return no data during trajectory generation.

Published cases contain only the runtime contract. Candidate draws, hidden tool
plans, rejection history, and generation provenance are written to a sidecar.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
import urllib.error
import urllib.request

from agent_env.settings import load_project_env
from agent_env.recovery import (
    FatalProviderError,
    FailureLedger,
    ProviderHealthExceeded,
    RecoveryContract,
    RollingFailureWindow,
    atomic_write_json,
    atomic_write_jsonl,
    ensure_recovery_manifest,
    file_hashes,
    is_transient_provider_error,
    is_fatal_provider_error,
    quarantine_artifact,
    require_model,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TOOL_POOL = PROJECT_DIR / "config/scenario_tool_pool.json"
SCENARIO_MODEL = "deepseek-v4-flash"

DOMAIN_DESCRIPTIONS = {
    "travel": (
        "Travel, local exploration, leisure, recreation, social outings, and "
        "experiences outside the person's usual routine."
    ),
    "health": (
        "Physical or mental health, well-being, symptoms, healthcare, "
        "medication, nutrition, and everyday health management."
    ),
    "shopping": (
        "Products, services, purchasing, consumption, comparison, ownership, "
        "replacement, and other everyday spending decisions."
    ),
    "career_learning": (
        "Work, education, skills, employment, professional development, "
        "research, and changes in the person's learning or career path."
    ),
}
DOMAIN_TASK_BOUNDARIES = {
    "travel": (
        "The primary objective must concern an actual or considered trip, outing, "
        "destination visit, recreation, or leisure experience."
    ),
    "health": (
        "The primary objective must concern health, care, medication, symptoms, "
        "nutrition, or well-being."
    ),
    "shopping": (
        "The primary objective must concern acquiring, comparing, replacing, "
        "repairing, returning, or paying for a consumer product or service."
    ),
    "career_learning": (
        "The primary objective must concern work, education, research, skill "
        "development, professional output, or an employment decision."
    ),
}
DOMAINS = tuple(DOMAIN_DESCRIPTIONS)
DEFAULT_CANDIDATE_TOOL_PROBABILITY = 0.25
SCENARIO_PIPELINE_ID = "profile_domain_candidate_task"
SCENARIO_PROMPT_ID = "profile_conditioned_task_planner"

# These discovered tools have a known non-working runtime contract in the
# configured environment, so they must not enter a production candidate draw.
UNUSABLE_TOOL_NAMES = {
    "OneBusAway:onebusaway_search_routes",  # hosted Puget endpoint returns 404
    "Paper Search:download_pubmed",         # implementation reports unsupported
    "Paper Search:read_pubmed_paper",       # implementation reports unsupported
}


SYSTEM_PROMPT = """# Role
You design and audit grounded synthetic user tasks for a tool-using Agent.

# Boundaries
- Treat supplied profiles and capability schemas as authoritative inputs.
- Keep hidden execution plans separate from the user's task.
- Follow the requested JSON contract exactly."""

PLANNER_PROMPT_TEMPLATE = """# Role
You are a profile-conditioned task planner.

# Task
Decide whether this synthetic user could naturally need one coherent Agent task
that a non-empty subset of the exposed tools can reasonably attempt.
If yes, write the structured user task and the complete hidden tool plan in one
response. If no, reject this candidate draw so another subset can be sampled.

# Input
Scenario ID: {scenario_id}
Target domain: {domain}: {domain_description}
Domain boundary: {domain_boundary}
Planning attempt: {attempt}

## Complete synthetic profile
<PROFILE_JSON>
{profile}
</PROFILE_JSON>

## Random candidate tool subset with exact schemas
<TOOLS_JSON>
{tools}
</TOOLS_JSON>

## Previous rejection feedback
{previous_feedback}

# Acceptance rules
- Treat the random candidate set as an option menu, not a bundle. You are never
  required to use every candidate. Ignore irrelevant or redundant candidates.
  Reject only when no non-empty plausible subset exists. One sufficient
  tool is allowed; there is no minimum tool count or "non-triviality" rule.
- The task must be plausible for this particular profile and domain. You may
  synthesize an ordinary near-term trigger that does not contradict the profile.
- The domain boundary governs the primary objective, not merely a supporting
  detail. The trigger need not already be stated in the profile.
- Keep one coherent decision or deliverable. Do not concatenate unrelated needs
  merely to consume more candidate tools.
- Use only profile facts that materially improve plausibility or constraints.
- The public user_task must never contain the person's name, phone number,
  email address, government identifier, or unrelated sensitive facts.
- Select a non-empty subset of candidate tools whose documented capabilities
  provide a reasonable way to attempt the task.
- A tool call may fail, return no results, lack geographic coverage, or expose
  sparse live data. That is an allowed trajectory outcome, not a reason by
  itself to reject the scenario or add a guaranteed-success fallback.
- Emit exactly one tool_role per selected tool, no duplicate selected tools,
  contiguous order values starting at 1, and a non-null binding for every field
  listed in that tool's input_schema.required. Preserve required object shapes.
- Do not request booking, purchasing, submitting, diagnosing, or another action
  that the selected read-only capabilities cannot perform.
- The public user_task must not mention MCP, tools, APIs, servers, calls,
  workflows, call order, or execution steps.
- Use relative time only: today, tomorrow, in six days, next week, later this
  month, and similar expressions. Never emit a year, ISO date, month-day date,
  or another absolute calendar date anywhere, including hidden input bindings.
- Hidden input bindings describe concrete literals, relative task time, user
  values, or prior call outputs. They are audit metadata, not runtime arguments.

# Output
Return JSON only.

For acceptance:
{{
  "accepted": true,
  "reason": "why this user could plausibly need this task",
  "user_task": {{
    "goal": "one concrete user-facing goal",
    "context": "the relevant situation and trigger",
    "constraints": ["only task-relevant constraints"],
    "expected_result": "the specific useful result the Agent should deliver"
  }},
  "selected_tools": ["Server:tool_name"],
  "tool_roles": [
    {{
      "qualified_name": "Server:tool_name",
      "order": 1,
      "necessary_use": "why this exact capability is required",
      "required_input_bindings": {{
        "required_schema_field": "literal, relative task time, user value, or prior output"
      }}
    }}
  ]
}}

For rejection:
{{
  "accepted": false,
  "reason": "why no natural task fits this profile and candidate draw",
  "user_task": null,
  "selected_tools": [],
  "tool_roles": []
}}"""

@dataclass(frozen=True)
class ToolSpec:
    server_name: str
    tool_name: str
    description: str
    input_schema: Mapping[str, Any]
    domains: tuple[str, ...]
    qualified_name: str
    scope_constraints: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ToolSpec":
        server_name = str(value.get("server_name") or value.get("server") or "")
        tool_name = str(value.get("tool_name") or value.get("name") or "")
        if not server_name or not tool_name:
            raise ValueError("Tool pool entries require server_name and tool_name")
        raw_domains = value.get("domains", ())
        if not isinstance(raw_domains, (list, tuple)) or not all(
            isinstance(item, str) and item in DOMAINS for item in raw_domains
        ):
            raise ValueError(f"{server_name}:{tool_name} has invalid domains")
        input_schema = value.get("input_schema") or value.get("inputSchema") or {}
        if not isinstance(input_schema, Mapping):
            raise ValueError(f"{server_name}:{tool_name} has invalid input_schema")
        raw_scope_constraints = value.get("scope_constraints", ())
        if not isinstance(raw_scope_constraints, (list, tuple)) or not all(
            isinstance(item, str) and item.strip() for item in raw_scope_constraints
        ):
            raise ValueError(
                f"{server_name}:{tool_name} has invalid scope_constraints"
            )
        return cls(
            server_name=server_name,
            tool_name=tool_name,
            qualified_name=str(
                value.get("qualified_name") or f"{server_name}:{tool_name}"
            ),
            description=str(value.get("description") or ""),
            input_schema=dict(input_schema),
            domains=tuple(dict.fromkeys(raw_domains)),
            scope_constraints=tuple(
                " ".join(item.split()) for item in raw_scope_constraints
            ),
        )

    def prompt_record(self) -> dict[str, Any]:
        return {
            "server_name": self.server_name,
            "tool_name": self.tool_name,
            "qualified_name": self.qualified_name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "scope_constraints": list(self.scope_constraints),
        }

    def case_record(self) -> dict[str, str]:
        return {
            "server_name": self.server_name,
            "tool_name": self.tool_name,
            "qualified_name": self.qualified_name,
        }


@dataclass(frozen=True)
class GenerationConfig:
    model: str
    base_url: str
    api_key: str
    system_prompt: str = SYSTEM_PROMPT
    temperature: float = 1.0
    max_tokens: int = 3072
    timeout: float = 90.0
    retries: int = 2
    workers: int = 4
    candidate_tool_probability: float = DEFAULT_CANDIDATE_TOOL_PROBABILITY
    max_task_construction_attempts: int = 8
    seed: int = 42
    api_keys: tuple[str, ...] = ()
    api_key_rpms: tuple[int, ...] = ()
    api_key_models: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_tokens <= 0 or self.timeout <= 0 or self.workers <= 0:
            raise ValueError("max_tokens, timeout, and workers must be positive")
        if self.retries < 0:
            raise ValueError("retries cannot be negative")
        if not 0 < self.candidate_tool_probability <= 1:
            raise ValueError("candidate_tool_probability must be in (0, 1]")
        if self.max_task_construction_attempts <= 0:
            raise ValueError("max_task_construction_attempts must be positive")


def profile_for_prompt(profile: Mapping[str, Any]) -> dict[str, Any]:
    return dict(profile)


def load_tool_pool(path: Path = DEFAULT_TOOL_POOL) -> list[ToolSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("tools") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError("Tool pool must be a list or an object with a tools list")
    tools: list[ToolSpec] = []
    for item in values:
        tool = ToolSpec.from_mapping(item)
        if tool.qualified_name in UNUSABLE_TOOL_NAMES:
            continue
        tools.append(tool)
    keys = [(tool.server_name, tool.tool_name) for tool in tools]
    if len(keys) != len(set(keys)):
        raise ValueError("Tool pool contains duplicate server/tool pairs")
    for domain in DOMAINS:
        if not any(domain in tool.domains for tool in tools):
            raise ValueError(f"Tool pool has no tools for domain {domain}")
    return tools


def sample_candidate_tool_subset(
    tool_pool: Iterable[ToolSpec],
    *,
    domain: str,
    inclusion_probability: float,
    seed: int,
    scenario_id: str,
    draw_attempt: int,
) -> list[ToolSpec]:
    if domain not in DOMAIN_DESCRIPTIONS:
        raise ValueError(f"Unsupported domain: {domain}")
    if not 0 < inclusion_probability <= 1:
        raise ValueError("inclusion_probability must be in (0, 1]")
    if draw_attempt <= 0:
        raise ValueError("draw_attempt must be positive")
    candidates = sorted(
        (tool for tool in tool_pool if domain in tool.domains),
        key=lambda tool: (tool.server_name, tool.tool_name),
    )
    if not candidates:
        raise ValueError(f"Tool pool has no tools for domain {domain}")
    digest = hashlib.sha256(
        (
            f"{seed}:{scenario_id}:{domain}:{draw_attempt}:"
            "candidate_tool_bernoulli"
        ).encode("utf-8")
    ).digest()
    rng = random.Random(int.from_bytes(digest[:16], "big"))
    selected = [tool for tool in candidates if rng.random() < inclusion_probability]
    if not selected:
        selected = [rng.choice(candidates)]
    rng.shuffle(selected)
    return selected


def format_tools_for_prompt(tools: Iterable[ToolSpec]) -> str:
    return json.dumps(
        [tool.prompt_record() for tool in tools], ensure_ascii=False, indent=2
    )


def build_profile_conditioned_planner_prompt(
    profile: Mapping[str, Any],
    domain: str,
    candidate_tools: Iterable[ToolSpec],
    *,
    scenario_id: str,
    attempt: int,
    previous_feedback: str = "None; this is the first attempt.",
) -> str:
    if domain not in DOMAIN_DESCRIPTIONS:
        raise ValueError(f"Unsupported domain: {domain}")
    candidates = list(candidate_tools)
    if not candidates or any(domain not in tool.domains for tool in candidates):
        raise ValueError("Planner requires non-empty target-domain candidates")
    return PLANNER_PROMPT_TEMPLATE.format(
        scenario_id=scenario_id,
        domain=domain,
        domain_description=DOMAIN_DESCRIPTIONS[domain],
        domain_boundary=DOMAIN_TASK_BOUNDARIES[domain],
        attempt=attempt,
        profile=json.dumps(profile_for_prompt(profile), ensure_ascii=False, indent=2),
        tools=format_tools_for_prompt(candidates),
        previous_feedback=previous_feedback,
    )


def strip_model_wrapper(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def parse_json_object(text: str, stage: str) -> dict[str, Any]:
    try:
        value = json.loads(strip_model_wrapper(text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM returned invalid {stage} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"LLM {stage} output must be a JSON object")
    return value


def _normalize_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{field} must be a non-empty string")
    return " ".join(value.split())


def _parse_user_task(value: Any, profile: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "goal", "context", "constraints", "expected_result"
    }:
        raise RuntimeError("user_task must contain goal, context, constraints, expected_result")
    constraints = value.get("constraints")
    if not isinstance(constraints, list) or not all(
        isinstance(item, str) and item.strip() for item in constraints
    ):
        raise RuntimeError("user_task.constraints must be a string list")
    return {
        "goal": _normalize_text(value.get("goal"), "user_task.goal"),
        "context": _normalize_text(value.get("context"), "user_task.context"),
        "constraints": [" ".join(item.split()) for item in constraints],
        "expected_result": _normalize_text(
            value.get("expected_result"), "user_task.expected_result"
        ),
    }


def _parse_roles(
    raw_roles: Any,
    selected: Sequence[ToolSpec],
) -> list[dict[str, Any]]:
    if not isinstance(raw_roles, list) or len(raw_roles) != len(selected):
        raise RuntimeError("tool_roles must cover every selected tool")
    tools_by_name = {tool.qualified_name: tool for tool in selected}
    roles: list[dict[str, Any]] = []
    for item in raw_roles:
        if not isinstance(item, Mapping):
            raise RuntimeError("Each tool role must be an object")
        if set(item) != {
            "qualified_name", "order", "necessary_use", "required_input_bindings"
        }:
            raise RuntimeError("Each tool role must use the expected schema")
        name = item.get("qualified_name")
        order = item.get("order")
        bindings = item.get("required_input_bindings")
        if (
            not isinstance(name, str)
            or name not in tools_by_name
            or not isinstance(order, int)
            or isinstance(order, bool)
            or order <= 0
            or not isinstance(bindings, Mapping)
        ):
            raise RuntimeError("Invalid tool role name, order, or bindings")
        necessary_use = _normalize_text(item.get("necessary_use"), "necessary_use")
        normalized_bindings: dict[str, Any] = {}
        for field, source in bindings.items():
            if (
                not isinstance(field, str)
                or not field.strip()
                or source is None
                or (isinstance(source, str) and not source.strip())
            ):
                raise RuntimeError("Invalid required input binding")
            normalized_bindings[field.strip()] = (
                _normalize_text(source, f"binding {field}")
                if isinstance(source, str)
                else source
            )
        required = tools_by_name[name].input_schema.get("required", [])
        required_fields = (
            {str(field) for field in required} if isinstance(required, list) else set()
        )
        if not required_fields.issubset(normalized_bindings):
            raise RuntimeError(
                f"required_input_bindings for {name} are missing "
                f"{sorted(required_fields - set(normalized_bindings))}"
            )
        roles.append(
            {
                "qualified_name": name,
                "order": order,
                "necessary_use": necessary_use,
                "required_input_bindings": normalized_bindings,
            }
        )
    names = [role["qualified_name"] for role in roles]
    if len(names) != len(set(names)) or set(names) != set(tools_by_name):
        raise RuntimeError("tool_roles must cover selected tools exactly once")
    if sorted(role["order"] for role in roles) != list(range(1, len(roles) + 1)):
        raise RuntimeError("tool role order must be contiguous from one")
    return sorted(roles, key=lambda role: role["order"])


def parse_planner_decision(
    text: str,
    candidate_tools: Iterable[ToolSpec],
    *,
    profile: Mapping[str, Any],
    domain: str,
) -> dict[str, Any]:
    value = parse_json_object(text, "planner")
    if set(value) != {
        "accepted", "reason", "user_task", "selected_tools", "tool_roles"
    }:
        raise RuntimeError("Planner output must use the expected schema")
    accepted = value.get("accepted")
    reason = _normalize_text(value.get("reason"), "reason")
    if not isinstance(accepted, bool):
        raise RuntimeError("Planner output requires boolean accepted")
    if not accepted:
        if value.get("selected_tools") not in ([], None) or value.get("tool_roles") not in ([], None):
            raise RuntimeError("Rejected planner decisions cannot select tools")
        if value.get("user_task") is not None:
            raise RuntimeError("Rejected planner decisions require null user_task")
        return {"accepted": False, "reason": reason}

    candidates = list(candidate_tools)
    by_name = {tool.qualified_name: tool for tool in candidates}
    raw_selected = value.get("selected_tools")
    if not isinstance(raw_selected, list) or not raw_selected or not all(
        isinstance(name, str) for name in raw_selected
    ):
        raise RuntimeError("Accepted planner decisions require selected_tools")
    canonical = {
        name.casefold(): name for name in by_name
    }
    selected_names = [canonical.get(name.casefold(), name) for name in raw_selected]
    if (
        len(selected_names) != len(set(selected_names))
        or any(name not in by_name for name in selected_names)
    ):
        raise RuntimeError("selected_tools must be a unique candidate subset")
    selected = [by_name[name] for name in selected_names]
    user_task = _parse_user_task(value.get("user_task"), profile)
    roles = _parse_roles(value.get("tool_roles"), selected)
    return {
        "accepted": True,
        "reason": reason,
        "user_task": user_task,
        "selected_tools": selected,
        "tool_roles": roles,
    }


def build_evaluation_criteria(user_task: Mapping[str, Any]) -> list[str]:
    return [
        f"The assistant fulfills the user's goal: {user_task['goal']}",
        f"The delivered result satisfies: {user_task['expected_result']}",
        "External factual claims are grounded in successful relevant MCP tool results.",
    ]


def make_case(job: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": job["scenario_id"],
        "profile_id": job["profile_id"],
        "domain": job["domain"],
        "user_task": decision["user_task"],
        "evaluation_criteria": build_evaluation_criteria(decision["user_task"]),
    }


def validate_case_record(record: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed = {
        "scenario_id", "profile_id", "domain", "user_task",
        "evaluation_criteria",
    }
    if set(record) != allowed:
        errors.append(f"case fields must be exactly {sorted(allowed)}")
    for field in ("scenario_id", "profile_id", "domain"):
        if not isinstance(record.get(field), str) or not str(record[field]).strip():
            errors.append(f"missing {field}")
    if record.get("domain") not in DOMAINS:
        errors.append("unsupported domain")
    task = record.get("user_task")
    if not isinstance(task, Mapping) or set(task) != {
        "goal", "context", "constraints", "expected_result"
    }:
        errors.append("invalid user_task")
    else:
        if not all(isinstance(task.get(field), str) and task[field].strip() for field in ("goal", "context", "expected_result")):
            errors.append("invalid user_task text")
        if not isinstance(task.get("constraints"), list) or not all(
            isinstance(item, str) and item.strip() for item in task.get("constraints", [])
        ):
            errors.append("invalid constraints")
    criteria = record.get("evaluation_criteria")
    if not isinstance(criteria, list) or len(criteria) != 3 or not all(
        isinstance(item, str) and item.strip() for item in criteria
    ):
        errors.append("invalid evaluation_criteria")
    elif isinstance(task, Mapping) and criteria != build_evaluation_criteria(task):
        errors.append("evaluation_criteria are not deterministically derived")
    return errors


def make_jobs(
    profiles: list[dict[str, Any]], domains: tuple[str, ...] = DOMAINS
) -> list[dict[str, Any]]:
    unsupported = [domain for domain in domains if domain not in DOMAINS]
    if unsupported:
        raise ValueError(f"Unsupported domains: {unsupported}")
    jobs: list[dict[str, Any]] = []
    for profile_index, profile in enumerate(profiles, start=1):
        profile_id = str(profile.get("profile_id") or f"P{profile_index:04d}")
        for domain in domains:
            number = (profile_index - 1) * len(DOMAINS) + DOMAINS.index(domain) + 1
            jobs.append(
                {
                    "order": number,
                    "scenario_id": f"S{number:04d}",
                    "profile_id": profile_id,
                    "profile": profile,
                    "domain": domain,
                }
            )
    return jobs


def read_first_profiles(path: Path, n: int) -> list[dict[str, Any]]:
    if n <= 0:
        raise ValueError("n must be positive")
    profiles: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Profile on line {line_number} is not an object")
            profiles.append(value)
            if len(profiles) == n:
                break
    if len(profiles) != n:
        raise ValueError(f"Requested {n} profiles, found {len(profiles)}")
    return profiles


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Invalid record in {path}:{line_number}")
            records.append(value)
    return records


read_scenario_records = read_jsonl


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    atomic_write_jsonl(path, records)


def _append_jsonl(handle: Any, value: Mapping[str, Any]) -> None:
    if handle is None:
        return
    handle.write(json.dumps(dict(value), ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def normalize_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def call_openai_compatible(
    prompt: str, config: GenerationConfig, *, api_key: str | None = None
) -> str:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "response_format": {"type": "json_object"},
    }
    if config.model.casefold() in {"glm-5.2", "glm5-2"}:
        payload["reasoning_effort"] = "none"
    request = urllib.request.Request(
        normalize_base_url(config.base_url),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key or config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(config.retries + 1):
        try:
            direct_opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({})
            )
            with direct_opener.open(request, timeout=config.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            choice = body["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError("LLM output was truncated by max_tokens")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("LLM returned empty text")
            return content.strip()
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            KeyError,
            IndexError,
            json.JSONDecodeError,
            RuntimeError,
        ) as exc:
            last_error = exc
            if attempt < config.retries:
                time.sleep(2**attempt)
    raise RuntimeError(
        f"LLM request failed after {config.retries + 1} attempts: {last_error}"
    )


class MultiKeyScenarioCaller:
    def __init__(self, config: GenerationConfig) -> None:
        self.config = config
        self.api_keys = config.api_keys or (config.api_key,)
        self.api_key_rpms = config.api_key_rpms or (0,) * len(self.api_keys)
        self.api_key_models = config.api_key_models or (config.model,) * len(self.api_keys)
        if len(self.api_key_rpms) != len(self.api_keys):
            raise ValueError("api_key_rpms must provide one value per API key")
        if len(self.api_key_models) != len(self.api_keys):
            raise ValueError("api_key_models must provide one model alias per API key")
        self._condition = threading.Condition()
        self._provider_health = RollingFailureWindow()
        self._halted = threading.Event()
        self._next_request_times = [0.0] * len(self.api_keys)
        self._cursor = 0

    def _acquire_key(self) -> tuple[int, str]:
        with self._condition:
            while True:
                now = time.monotonic()
                for offset in range(len(self.api_keys)):
                    index = (self._cursor + offset) % len(self.api_keys)
                    rpm = self.api_key_rpms[index]
                    if rpm <= 0 or self._next_request_times[index] <= now:
                        if rpm > 0:
                            self._next_request_times[index] = max(
                                now, self._next_request_times[index]
                            ) + (60.0 / rpm) * 1.2
                        self._cursor = (index + 1) % len(self.api_keys)
                        return index, self.api_keys[index]
                wait_seconds = min(
                    max(0.001, ready - now)
                    for ready, rpm in zip(self._next_request_times, self.api_key_rpms)
                    if rpm > 0
                )
                self._condition.wait(timeout=wait_seconds)

    def __call__(self, prompt: str, config: GenerationConfig) -> str:
        if self._halted.is_set():
            raise ProviderHealthExceeded(
                "provider health window exceeded 5%; resume with lower concurrency"
            )
        single_attempt = replace(config, retries=0)
        last_error: Exception | None = None
        for attempt in range(config.retries + 1):
            try:
                key_index, api_key = self._acquire_key()
                result = call_openai_compatible(
                    prompt,
                    replace(single_attempt, model=self.api_key_models[key_index]),
                    api_key=api_key,
                )
                self._provider_health.record(False)
                return result
            except Exception as exc:
                if isinstance(exc, (ProviderHealthExceeded, FatalProviderError)):
                    raise
                if is_fatal_provider_error(exc):
                    self._halted.set()
                    raise FatalProviderError(str(exc)) from exc
                if self._provider_health.record(
                    is_transient_provider_error(exc)
                ):
                    self._halted.set()
                    raise ProviderHealthExceeded(
                        "provider health window exceeded 5%; resume with lower "
                        f"concurrency: {exc}"
                    ) from exc
                last_error = exc
                if attempt < config.retries:
                    time.sleep(2**attempt)
        raise RuntimeError(
            f"LLM request failed after {config.retries + 1} attempts: {last_error}"
        )


def _terminal_failure_record(
    state: Mapping[str, Any], config: GenerationConfig
) -> dict[str, Any]:
    job = state["job"]
    return {
        "scenario_id": job["scenario_id"],
        "profile_id": job["profile_id"],
        "domain": job["domain"],
        "attempts": state["history"],
        "recovery_round": int(state.get("recovery_round", 0)),
        "error": "maximum planner attempts exhausted",
        "generation": _generation_metadata(config),
    }


def _generation_metadata(config: GenerationConfig) -> dict[str, Any]:
    return {
        "pipeline": SCENARIO_PIPELINE_ID,
        "prompt": SCENARIO_PROMPT_ID,
        "model": config.model,
        "seed": config.seed,
        "candidate_tool_probability": config.candidate_tool_probability,
        "max_task_construction_attempts": config.max_task_construction_attempts,
    }


def _generation_is_current(
    generation: Any, config: GenerationConfig
) -> bool:
    return (
        isinstance(generation, Mapping)
        and generation.get("pipeline") == SCENARIO_PIPELINE_ID
        and generation.get("prompt") == SCENARIO_PROMPT_ID
        and generation.get("model") == config.model
        and generation.get("seed") == config.seed
        and generation.get("candidate_tool_probability")
        == config.candidate_tool_probability
        and generation.get("max_task_construction_attempts")
        == config.max_task_construction_attempts
    )


def _trace_is_current(
    case: Mapping[str, Any], trace: Mapping[str, Any], config: GenerationConfig
) -> bool:
    generation = trace.get("generation")
    roles = trace.get("tool_roles")
    role_names = {
        role.get("qualified_name")
        for role in roles or []
        if isinstance(role, Mapping)
    }
    role_orders = [
        role.get("order") for role in roles or [] if isinstance(role, Mapping)
    ]
    attempts = trace.get("attempts")
    accepted_attempt = trace.get("accepted_attempt")
    final_attempt = attempts[-1] if isinstance(attempts, list) and attempts else None
    candidate_names = {
        tool.get("qualified_name")
        for tool in (final_attempt or {}).get("candidate_tools", [])
        if isinstance(tool, Mapping)
    }
    return (
        not validate_case_record(case)
        and trace.get("scenario_id") == case.get("scenario_id")
        and trace.get("profile_id") == case.get("profile_id")
        and trace.get("domain") == case.get("domain")
        and isinstance(roles, list)
        and bool(role_names)
        and len(roles) == len(role_names)
        and all(isinstance(order, int) and not isinstance(order, bool) for order in role_orders)
        and sorted(role_orders) == list(range(1, len(roles) + 1))
        and isinstance(attempts, list)
        and isinstance(final_attempt, Mapping)
        and final_attempt.get("decision") == "accepted"
        and role_names.issubset(candidate_names)
        and isinstance(accepted_attempt, int)
        and accepted_attempt == final_attempt.get("attempt")
        and accepted_attempt > 0
        and _generation_is_current(generation, config)
    )


def generate_scenarios_resumable(
    profiles: list[dict[str, Any]],
    config: GenerationConfig,
    *,
    existing_cases: Iterable[dict[str, Any]] = (),
    existing_traces: Iterable[dict[str, Any]] = (),
    existing_terminal_failures: Iterable[dict[str, Any]] = (),
    retry_terminal_failures: Iterable[dict[str, Any]] = (),
    case_checkpoint_path: Path | None = None,
    trace_checkpoint_path: Path | None = None,
    caller: Callable[[str, GenerationConfig], str] = call_openai_compatible,
    fail_fast: bool = False,
    progress_every: int = 20,
    domains: tuple[str, ...] = DOMAINS,
    tool_pool: Iterable[ToolSpec] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    jobs = make_jobs(profiles, domains)
    jobs_by_id = {job["scenario_id"]: job for job in jobs}
    tools = list(tool_pool if tool_pool is not None else load_tool_pool())
    traces_by_id = {
        str(trace.get("scenario_id")): trace
        for trace in existing_traces
        if trace.get("scenario_id") in jobs_by_id
    }
    cases_by_id: dict[str, dict[str, Any]] = {}
    for case in existing_cases:
        scenario_id = str(case.get("scenario_id") or "")
        trace = traces_by_id.get(scenario_id)
        if trace is not None and _trace_is_current(case, trace, config):
            cases_by_id[scenario_id] = case

    failures = [
        failure
        for failure in existing_terminal_failures
        if failure.get("scenario_id") in jobs_by_id
        and failure.get("scenario_id") not in cases_by_id
        and _generation_is_current(failure.get("generation"), config)
    ]
    terminal_ids = {str(failure.get("scenario_id")) for failure in failures}

    retry_by_id = {
        str(failure.get("scenario_id")): failure
        for failure in retry_terminal_failures
        if failure.get("scenario_id") in jobs_by_id
        and failure.get("scenario_id") not in cases_by_id
        and _generation_is_current(failure.get("generation"), config)
    }
    states: dict[str, dict[str, Any]] = {}
    for job in jobs:
        scenario_id = job["scenario_id"]
        if scenario_id in cases_by_id or scenario_id in terminal_ids:
            continue
        previous = retry_by_id.get(scenario_id)
        history = list(previous.get("attempts", [])) if previous else []
        attempt_numbers = [
            int(item.get("attempt", 0))
            for item in history
            if isinstance(item, Mapping)
            and isinstance(item.get("attempt"), int)
        ]
        recovery_round = (
            int(previous.get("recovery_round", 0)) + 1 if previous else 0
        )
        previous_feedback = "None; this is the first attempt."
        if history:
            latest = history[-1]
            previous_feedback = str(
                latest.get("reason")
                or "Previous recovery round exhausted."
            )
        states[scenario_id] = {
            "job": job,
            "attempt": max(attempt_numbers, default=0) + 1,
            "attempt_limit": (recovery_round + 1)
            * config.max_task_construction_attempts,
            "history": history,
            "previous_feedback": previous_feedback,
            "recovery_round": recovery_round,
        }
    case_handle = trace_handle = None
    if states and case_checkpoint_path is not None:
        case_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        case_handle = case_checkpoint_path.open("a", encoding="utf-8")
    if states and trace_checkpoint_path is not None:
        trace_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        trace_handle = trace_checkpoint_path.open("a", encoding="utf-8")

    completed_new = 0
    try:
        while states:
            def plan_one(state: dict[str, Any]) -> tuple[str, list[ToolSpec], dict[str, Any] | Exception]:
                job = state["job"]
                candidates = sample_candidate_tool_subset(
                    tools,
                    domain=job["domain"],
                    inclusion_probability=config.candidate_tool_probability,
                    seed=config.seed,
                    scenario_id=job["scenario_id"],
                    draw_attempt=state["attempt"],
                )
                prompt = build_profile_conditioned_planner_prompt(
                    job["profile"],
                    job["domain"],
                    candidates,
                    scenario_id=job["scenario_id"],
                    attempt=state["attempt"],
                    previous_feedback=state["previous_feedback"],
                )
                try:
                    decision = parse_planner_decision(
                        caller(prompt, config),
                        candidates,
                        profile=job["profile"],
                        domain=job["domain"],
                    )
                    return job["scenario_id"], candidates, decision
                except (ProviderHealthExceeded, FatalProviderError):
                    raise
                except Exception as exc:
                    return job["scenario_id"], candidates, exc

            with ThreadPoolExecutor(max_workers=config.workers) as executor:
                futures = {executor.submit(plan_one, state): sid for sid, state in states.items()}
                for future in as_completed(futures):
                    scenario_id, candidates, result = future.result()
                    state = states[scenario_id]
                    attempt_record = {
                        "attempt": state["attempt"],
                        "recovery_round": state["recovery_round"],
                        "candidate_tools": [tool.case_record() for tool in candidates],
                    }
                    if isinstance(result, Exception):
                        feedback = str(result)
                        attempt_record.update({"decision": "planner_invalid", "reason": feedback})
                        state["history"].append(attempt_record)
                        state["previous_feedback"] = feedback
                        state["attempt"] += 1
                    elif not result["accepted"]:
                        feedback = result["reason"]
                        attempt_record.update({"decision": "planner_rejected", "reason": feedback})
                        state["history"].append(attempt_record)
                        state["previous_feedback"] = feedback
                        state["attempt"] += 1
                    else:
                        attempt_record.update({
                            "decision": "accepted",
                            "reason": result["reason"],
                        })
                        state["history"].append(attempt_record)
                        job = state["job"]
                        case = make_case(job, result)
                        trace = {
                            "scenario_id": scenario_id,
                            "profile_id": job["profile_id"],
                            "domain": job["domain"],
                            "attempts": state["history"],
                            "accepted_attempt": state["attempt"],
                            "planner_reason": result["reason"],
                            "tool_roles": result["tool_roles"],
                            "generation": _generation_metadata(config),
                        }
                        cases_by_id[scenario_id] = case
                        traces_by_id[scenario_id] = trace
                        _append_jsonl(case_handle, case)
                        _append_jsonl(trace_handle, trace)
                        states.pop(scenario_id)
                        completed_new += 1
                        if progress_every > 0 and completed_new % progress_every == 0:
                            print(
                                f"Scenario progress: new={completed_new} "
                                f"total={len(cases_by_id)}/{len(jobs)} "
                                f"pending={len(states)} failed={len(failures)}",
                                flush=True,
                            )

            exhausted = [
                sid for sid, state in states.items()
                if state["attempt"] > state["attempt_limit"]
            ]
            for scenario_id in exhausted:
                state = states.pop(scenario_id)
                failures.append(_terminal_failure_record(state, config))
            if fail_fast and failures:
                break
    finally:
        if case_handle is not None:
            case_handle.close()
        if trace_handle is not None:
            trace_handle.close()

    order = {job["scenario_id"]: job["order"] for job in jobs}
    cases = sorted(cases_by_id.values(), key=lambda item: order[item["scenario_id"]])
    traces = sorted(
        (trace for sid, trace in traces_by_id.items() if sid in cases_by_id),
        key=lambda item: order[item["scenario_id"]],
    )
    return cases, traces, failures


def generate_scenarios(
    profiles: list[dict[str, Any]],
    config: GenerationConfig,
    caller: Callable[[str, GenerationConfig], str] = call_openai_compatible,
    domains: tuple[str, ...] = DOMAINS,
    tool_pool: Iterable[ToolSpec] | None = None,
) -> list[dict[str, Any]]:
    cases, _, failures = generate_scenarios_resumable(
        profiles,
        config,
        caller=caller,
        domains=domains,
        tool_pool=tool_pool,
    )
    if failures:
        raise RuntimeError(f"Scenario generation failed: {failures[0]['error']}")
    return cases


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, required=True)
    parser.add_argument(
        "--input", type=Path, default=Path("artifacts/profile_pool/profiles.jsonl")
    )
    parser.add_argument("--max-recovery-rounds", type=int, default=3)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/scenario/cases.jsonl")
    )
    parser.add_argument("--trace-output", type=Path)
    parser.add_argument("--failure-output", type=Path)
    parser.add_argument("--tool-pool", type=Path, default=DEFAULT_TOOL_POOL)
    parser.add_argument(
        "--candidate-tool-probability",
        type=float,
        default=DEFAULT_CANDIDATE_TOOL_PROBABILITY,
    )
    parser.add_argument("--max-task-construction-attempts", type=int, default=8)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument(
        "--api-key-indexes",
        help=(
            "Optional 1-based comma-separated key slots selected from "
            "MY_MODEL_API_KEY plus MY_MODEL_API_KEYS."
        ),
    )
    parser.add_argument(
        "--api-key-rpms",
        help="Optional comma-separated per-selected-key RPM limits.",
    )
    parser.add_argument(
        "--api-key-models",
        help="Optional comma-separated per-selected-key provider model aliases.",
    )
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=3072)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--domains", nargs="+", choices=DOMAINS, default=list(DOMAINS))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-terminal-failures",
        action="store_true",
        help="Explicitly discard terminal failures from the current contract and retry them.",
    )
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--trace-checkpoint", type=Path)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--progress-every", type=int, default=20)
    parser.add_argument("--print-prompts", action="store_true")
    return parser


def _load_api_configuration(
    args: argparse.Namespace,
) -> tuple[str, str, tuple[str, ...], tuple[int, ...], tuple[str, ...]]:
    model = args.model or os.environ.get("MY_MODEL_NAME")
    base_url = args.base_url or os.environ.get("MY_MODEL_BASE_URL")
    primary_key = args.api_key or os.environ.get("MY_MODEL_API_KEY")
    extras = [
        value
        for value in re.split(r"[\s,;]+", os.environ.get("MY_MODEL_API_KEYS", "").strip())
        if value
    ]
    all_api_keys = tuple(dict.fromkeys(key for key in (primary_key, *extras) if key))
    missing = [
        name
        for name, value in (
            ("MY_MODEL_NAME", model),
            ("MY_MODEL_BASE_URL", base_url),
            ("MY_MODEL_API_KEY or MY_MODEL_API_KEYS", all_api_keys),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing LLM configuration: {', '.join(missing)}")

    raw_indexes = (
        args.api_key_indexes
        or os.environ.get("SCENARIO_API_KEY_INDEXES", "")
    ).strip()
    try:
        indexes = (
            tuple(int(value) for value in re.split(r"[\s,;]+", raw_indexes) if value)
            if raw_indexes
            else tuple(range(1, len(all_api_keys) + 1))
        )
    except ValueError as exc:
        raise ValueError("Scenario API key indexes must contain integers") from exc
    if (
        not indexes
        or len(set(indexes)) != len(indexes)
        or any(index < 1 or index > len(all_api_keys) for index in indexes)
    ):
        raise ValueError(
            "Scenario API key indexes must be unique 1-based slots in the configured key pool"
        )
    api_keys = tuple(all_api_keys[index - 1] for index in indexes)

    raw_all_rpms = os.environ.get("MY_MODEL_API_KEY_RPMS", "").strip()
    try:
        all_rpms = (
            tuple(int(value) for value in re.split(r"[\s,;]+", raw_all_rpms) if value)
            if raw_all_rpms
            else (0,) * len(all_api_keys)
        )
    except ValueError as exc:
        raise ValueError("MY_MODEL_API_KEY_RPMS must contain integers") from exc
    if len(all_rpms) != len(all_api_keys) or any(value < 0 for value in all_rpms):
        raise ValueError("MY_MODEL_API_KEY_RPMS must provide one non-negative value per key")
    raw_rpms = (
        args.api_key_rpms
        or os.environ.get("SCENARIO_API_KEY_RPMS", "")
    ).strip()
    try:
        rpms = (
            tuple(int(value) for value in re.split(r"[\s,;]+", raw_rpms) if value)
            if raw_rpms
            else tuple(all_rpms[index - 1] for index in indexes)
        )
    except ValueError as exc:
        raise ValueError("Scenario API key RPMs must contain integers") from exc
    if len(rpms) != len(api_keys) or any(value <= 0 for value in rpms):
        raise ValueError("Scenario API key RPMs must provide one positive value per selected key")

    raw_all_models = os.environ.get("MY_MODEL_API_KEY_MODELS", "").strip()
    all_models = (
        tuple(value for value in re.split(r"[\s,;]+", raw_all_models) if value)
        if raw_all_models
        else (str(model),) * len(all_api_keys)
    )
    if len(all_models) != len(all_api_keys):
        raise ValueError("MY_MODEL_API_KEY_MODELS must provide one model alias per key")
    raw_models = (
        args.api_key_models
        or os.environ.get("SCENARIO_API_KEY_MODELS", "")
    ).strip()
    models = (
        tuple(value for value in re.split(r"[\s,;]+", raw_models) if value)
        if raw_models
        else tuple(all_models[index - 1] for index in indexes)
    )
    if len(models) != len(api_keys):
        raise ValueError("Scenario API key models must provide one alias per selected key")
    return str(model), str(base_url), api_keys, rpms, models


def main() -> int:
    load_project_env(PROJECT_DIR)
    args = build_arg_parser().parse_args()
    try:
        profiles = read_first_profiles(args.input, args.n)
        tool_pool = load_tool_pool(args.tool_pool.resolve())
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.print_prompts:
        job = make_jobs(profiles, tuple(args.domains))[0]
        candidates = sample_candidate_tool_subset(
            tool_pool,
            domain=job["domain"],
            inclusion_probability=args.candidate_tool_probability,
            seed=args.seed,
            scenario_id=job["scenario_id"],
            draw_attempt=1,
        )
        print(build_profile_conditioned_planner_prompt(
            job["profile"], job["domain"], candidates,
            scenario_id=job["scenario_id"], attempt=1,
        ))
        return 0

    try:
        model, base_url, api_keys, rpms, models = _load_api_configuration(args)
        require_model(
            model,
            stage="scenario",
            expected_model=SCENARIO_MODEL,
        )
        if args.max_recovery_rounds < 0:
            raise ValueError("--max-recovery-rounds cannot be negative")
        config = GenerationConfig(
            model=model,
            base_url=base_url,
            api_key=api_keys[0],
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            retries=args.retries,
            workers=args.workers,
            candidate_tool_probability=args.candidate_tool_probability,
            max_task_construction_attempts=args.max_task_construction_attempts,
            seed=args.seed,
            api_keys=api_keys,
            api_key_rpms=rpms,
            api_key_models=models,
        )
        trace_output = args.trace_output or args.output.with_name("generation_traces.jsonl")
        failure_output = args.failure_output or args.output.with_name("terminal_failures.jsonl")
        case_checkpoint = args.checkpoint or args.output.with_suffix(args.output.suffix + ".checkpoint")
        trace_checkpoint = args.trace_checkpoint or trace_output.with_suffix(trace_output.suffix + ".checkpoint")
        # A pipeline invokes this CLI once per domain. Keep each domain's
        # immutable contract and ledger isolated even though the output files
        # share a directory.
        recovery_root = args.output.parent / "recovery" / args.output.stem
        run_id = args.run_id or args.output.parent.name
        recovery_manifest_path = recovery_root / "manifest.json"
        recovery_manifest = ensure_recovery_manifest(
            recovery_manifest_path,
            RecoveryContract(
                run_id=run_id,
                phase="scenario",
                model=model,
                prompt_version=SCENARIO_PROMPT_ID,
                seed=args.seed,
                input_hashes=file_hashes(
                    [args.input.resolve(), args.tool_pool.resolve()]
                ),
                config={
                    "n": args.n,
                    "domains": list(args.domains),
                    "candidate_tool_probability": args.candidate_tool_probability,
                    "max_task_construction_attempts": args.max_task_construction_attempts,
                    "api_key_count": len(api_keys),
                    "api_key_rpms": list(rpms),
                    "api_key_models": list(models),
                },
                required_model=SCENARIO_MODEL,
            ),
            workers=args.workers,
            resume=args.resume,
        )
        ledger = FailureLedger(recovery_root, phase="scenario", run_id=run_id)
        existing_cases: list[dict[str, Any]] = []
        existing_traces: list[dict[str, Any]] = []
        existing_failures: list[dict[str, Any]] = []
        retry_failures: list[dict[str, Any]] = []
        if args.resume:
            def recoverable_records(path: Path) -> list[dict[str, Any]]:
                try:
                    return read_jsonl(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    quarantine_artifact(
                        path,
                        recovery_root / "quarantine",
                        item_id=path.stem,
                        reason="invalid_jsonl",
                    )
                    return []

            existing_cases = [
                *recoverable_records(args.output),
                *recoverable_records(case_checkpoint),
            ]
            existing_traces = [
                *recoverable_records(trace_output),
                *recoverable_records(trace_checkpoint),
            ]
            loaded_failures = recoverable_records(failure_output)
            if args.retry_terminal_failures:
                for failure in loaded_failures:
                    next_round = int(failure.get("recovery_round", 0)) + 1
                    if next_round <= args.max_recovery_rounds:
                        retry_failures.append(failure)
                    else:
                        existing_failures.append(failure)
            else:
                existing_failures = loaded_failures
        else:
            case_checkpoint.unlink(missing_ok=True)
            trace_checkpoint.unlink(missing_ok=True)
        reused_ids = {
            str(case.get("scenario_id"))
            for case in existing_cases
            if case.get("scenario_id")
        }
        cases, traces, failures = generate_scenarios_resumable(
            profiles,
            config,
            existing_cases=existing_cases,
            existing_traces=existing_traces,
            existing_terminal_failures=existing_failures,
            retry_terminal_failures=retry_failures,
            case_checkpoint_path=case_checkpoint,
            trace_checkpoint_path=trace_checkpoint,
            caller=MultiKeyScenarioCaller(config),
            fail_fast=args.fail_fast,
            progress_every=args.progress_every,
            domains=tuple(args.domains),
            tool_pool=tool_pool,
        )
        write_jsonl(args.output, cases)
        write_jsonl(trace_output, traces)
        write_jsonl(failure_output, failures)
        for failure in failures:
            scenario_id = str(failure.get("scenario_id"))
            ledger.record_failure(
                scenario_id,
                error_type="scenario_generation",
                error=str(failure.get("error") or "terminal failure"),
                attempts=len(failure.get("attempts", [])),
                recovery_round=int(failure.get("recovery_round", 0)),
                metadata={
                    "profile_id": failure.get("profile_id"),
                    "domain": failure.get("domain"),
                },
            )
        for case in cases:
            ledger.resolve(str(case.get("scenario_id")))
        expected = len(profiles) * len(args.domains)
        completed_ids = {
            str(case.get("scenario_id"))
            for case in cases
            if case.get("scenario_id")
        }
        summary = {
            "schema_version": "1.0",
            "run_id": run_id,
            "phase": "scenario",
            "expected_count": expected,
            "completed_count": len(completed_ids),
            "reused_count": len(completed_ids & reused_ids),
            "failed_count": len(failures),
            "pending_count": max(0, expected - len(completed_ids) - len(failures)),
            "recovery_round": max(
                (int(item.get("recovery_round", 0)) for item in failures),
                default=0,
            ),
            "updated_at": time.time(),
        }
        atomic_write_json(recovery_root / "_batch_summary.json", summary)
        recovery_manifest.update(
            {
                "status": (
                    "completed"
                    if not failures and len(completed_ids) == expected
                    else "failed"
                ),
                "updated_at": time.time(),
                "completed_at": time.time(),
                "unresolved_failure_count": len(ledger.unresolved()),
            }
        )
        atomic_write_json(recovery_manifest_path, recovery_manifest)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 42 if isinstance(exc, FatalProviderError) else 1

    if not failures and len(cases) == expected:
        case_checkpoint.unlink(missing_ok=True)
        trace_checkpoint.unlink(missing_ok=True)
    print(
        f"Available {len(cases)}/{expected} cases from "
        f"{len(profiles)} profiles; failed={len(failures)} -> {args.output}"
    )
    return 1 if failures or len(cases) != expected else 0


if __name__ == "__main__":
    raise SystemExit(main())
