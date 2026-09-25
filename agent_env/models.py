"""Typed boundary objects shared by simulator, agent, env, and evaluator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from agent_env.execution_limits import ExecutionLimits


@dataclass(frozen=True)
class DomainSpec:
    name: str
    description: str
    core_servers: tuple[str, ...]
    optional_servers: tuple[str, ...] = ()
    tool_allowlists: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    coverage_note: str = ""

    def select_servers(self) -> list[str]:
        return list(dict.fromkeys((*self.core_servers, *self.optional_servers)))

    def select_tool_allowlists(self) -> dict[str, list[str]]:
        return {
            server: list(tools)
            for server, tools in self.tool_allowlists.items()
        }


@dataclass(frozen=True)
class TaskCase:
    scenario_id: str
    profile_id: str
    domain: str
    user_task: Mapping[str, Any]
    profile: Mapping[str, Any]
    evaluation_criteria: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunLimits:
    max_user_turns: int | None = None
    turn_timeout_seconds: int = 300
    simulator_max_tokens: int = 1024
    simulator_temperature: float = 1.3
    simulator_format_attempts: int = 3
    agent: ExecutionLimits = field(default_factory=ExecutionLimits)

    def __post_init__(self) -> None:
        for name in (
            "max_user_turns",
            "turn_timeout_seconds",
            "simulator_max_tokens",
            "simulator_format_attempts",
        ):
            value = getattr(self, name)
            if name == "max_user_turns" and value is None:
                continue
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 <= self.simulator_temperature <= 2:
            raise ValueError("simulator_temperature must be between 0 and 2")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None = None) -> "RunLimits":
        values = dict(values or {})
        agent_values = values.pop("agent", None)
        unknown = set(values) - {
            "max_user_turns",
            "turn_timeout_seconds",
            "simulator_max_tokens",
            "simulator_temperature",
            "simulator_format_attempts",
        }
        if unknown:
            raise ValueError(f"Unknown run limit(s): {sorted(unknown)}")
        simulator_temperature = values.pop("simulator_temperature", None)
        kwargs = {
            key: (
                None
                if key == "max_user_turns" and value is None
                else int(value)
            )
            for key, value in values.items()
        }
        if simulator_temperature is not None:
            kwargs["simulator_temperature"] = float(simulator_temperature)
        return cls(**kwargs, agent=ExecutionLimits.from_mapping(agent_values))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["agent"] = self.agent.to_dict()
        return value


@dataclass
class AgentTurnResult:
    response: str
    total_rounds: int = 0
    tool_calls: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    execution_results: list[dict[str, Any]] = field(default_factory=list)
    termination_reason: str | None = None
    filtered_tool_calls: int = 0
    error: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AgentTurnResult":
        executions = list(value.get("execution_results", []))
        return cls(
            response=str(value.get("response", "")),
            total_rounds=int(value.get("total_rounds", 0) or 0),
            tool_calls=int(value.get("tool_calls", len(executions)) or 0),
            total_tokens=int(value.get("total_tokens", 0) or 0),
            prompt_tokens=int(value.get("prompt_tokens", 0) or 0),
            completion_tokens=int(value.get("completion_tokens", 0) or 0),
            execution_results=executions,
            termination_reason=value.get("termination_reason"),
            filtered_tool_calls=int(value.get("filtered_tool_calls", 0) or 0),
            error=value.get("error"),
        )

    def to_turn_record(self) -> dict[str, Any]:
        value = asdict(self)
        value.update({"role": "assistant", "content": value.pop("response")})
        return value
