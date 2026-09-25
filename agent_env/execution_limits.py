"""Explicit execution budgets for one PydanticAI assistant turn."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ExecutionLimits:
    """Budgets that bound one assistant turn.

    Round and tool-call caps are optional so trajectories can terminate
    naturally.
    """

    max_rounds: int | None = None
    max_tool_calls: int | None = None
    max_total_tokens: int | None = None

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if (
                name in {"max_rounds", "max_tool_calls", "max_total_tokens"}
                and value is None
            ):
                continue
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, Any] | None = None
    ) -> "ExecutionLimits":
        if not values:
            return cls()
        known = set(cls.__dataclass_fields__)
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"Unknown execution limit(s): {sorted(unknown)}")
        return cls(
            **{
                key: (
                    None
                    if key in {"max_rounds", "max_tool_calls", "max_total_tokens"}
                    and value is None
                    else int(value)
                )
                for key, value in values.items()
            }
        )

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)
