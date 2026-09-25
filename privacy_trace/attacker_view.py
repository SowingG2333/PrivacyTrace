"""Build the tool-call view that a profile-inference attacker may observe."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Mapping


ATTACKER_VIEW_POLICY = "first_execution_keep_success_empty_parameter_errors"
RAW_ATTACKER_VIEW_POLICY = "raw_all_calls"
LOSSLESS_TOOL_CATALOG = "lossless_tool_catalog"
RAW_REPEATED = "raw_repeated"
ATTACKER_OBSERVATION_FORMAT = LOSSLESS_TOOL_CATALOG
ATTACKER_OBSERVATION_FORMATS = (LOSSLESS_TOOL_CATALOG, RAW_REPEATED)


def attacker_view_policy_for_format(observation_format: str) -> str:
    if observation_format == LOSSLESS_TOOL_CATALOG:
        return ATTACKER_VIEW_POLICY
    if observation_format == RAW_REPEATED:
        return RAW_ATTACKER_VIEW_POLICY
    raise ValueError(f"Unsupported attacker observation format: {observation_format}")


_INFRASTRUCTURE_ERROR_MARKERS = (
    "authentication",
    "bad gateway",
    "circuit breaker",
    "connection closed",
    "connection error",
    "connection refused",
    "connection reset",
    "credential",
    "dns",
    "forbidden",
    "gateway timeout",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "internal server error",
    "max retries",
    "maximum retry",
    "network error",
    "proxy error",
    "rate limit",
    "rate_limited",
    "read timed out",
    "request failed",
    "request timed out",
    "server busy",
    "service unavailable",
    "temporarily unavailable",
    "timeout",
    "too many requests",
    "transport error",
    "unauthorized",
)

_PARAMETER_ERROR_MARKERS = (
    "argument",
    "bad request",
    "cannot be in the past",
    "does not exist",
    "expected one of",
    "invalid",
    "is required",
    "missing parameter",
    "must be",
    "must not",
    "no_filters",
    "not found",
    "not supported",
    "out of range",
    "outside the allowed",
    "parameter",
    "provide at least one",
    "required field",
    "unsupported",
    "validstatecodes",
    "validation",
    "-32602",
    "-32007",
)


@dataclass(frozen=True)
class AttackerViewStats:
    raw_call_count: int
    visible_call_count: int
    removed_cached_duplicate_count: int
    removed_failed_call_count: int
    retained_empty_result_count: int
    retained_parameter_error_count: int

    @property
    def prior_equivalent(self) -> bool:
        return self.visible_call_count == 0

    @property
    def raw_zero_call_prior_baseline(self) -> bool:
        return self.raw_call_count == 0

    @property
    def filtered_to_prior_equivalent(self) -> bool:
        return self.raw_call_count > 0 and self.visible_call_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "prior_equivalent": self.prior_equivalent,
            "raw_zero_call_prior_baseline": self.raw_zero_call_prior_baseline,
            "filtered_to_prior_equivalent": self.filtered_to_prior_equivalent,
        }


@dataclass(frozen=True)
class AttackerView:
    calls: list[dict[str, Any]]
    stats: AttackerViewStats


@dataclass(frozen=True)
class ObservationFormatStats:
    observation_chars: int
    tool_catalog_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormattedObservation:
    text: str
    stats: ObservationFormatStats


@dataclass(frozen=True)
class AttackerObservation:
    view: AttackerView
    formatted: FormattedObservation
    view_policy: str
    observation_format: str


def _call_text(call: Mapping[str, Any]) -> str:
    values = [
        call.get("error"),
        call.get("returned_result"),
        call.get("result"),
    ]
    parts: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        if isinstance(value, str):
            parts.append(value)
        else:
            parts.append(json.dumps(value, ensure_ascii=False, default=str))
    return " ".join(parts).casefold()


def _is_cached_duplicate(call: Mapping[str, Any]) -> bool:
    return (
        call.get("cache_hit") is True
        or call.get("result_source") == "cache"
        or (
            call.get("mcp_executed") is False
            and int(call.get("duplicate_call_count") or 0) > 1
        )
    )


def _is_empty_result(call: Mapping[str, Any]) -> bool:
    return str(call.get("outcome") or "").casefold() == "empty"


def _is_parameter_error(call: Mapping[str, Any]) -> bool:
    if str(call.get("outcome") or "").casefold() != "application_error":
        return False
    text = _call_text(call)
    if any(marker in text for marker in _INFRASTRUCTURE_ERROR_MARKERS):
        return False
    return any(marker in text for marker in _PARAMETER_ERROR_MARKERS)


def _is_failed_call(call: Mapping[str, Any]) -> bool:
    outcome = str(call.get("outcome") or "").casefold()
    if outcome == "success" or call.get("success") is True:
        return False
    if _is_empty_result(call) or _is_parameter_error(call):
        return False
    if outcome in {"transport_error", "application_error", "stalled_tool_loop"}:
        return True
    if call.get("transport_success") is False:
        return True
    # Legacy records sometimes have no normalized outcome. A completed MCP
    # response remains observable unless the record explicitly says it failed.
    return call.get("success") is False


def prepare_attacker_view(tools_info: list[dict[str, Any]]) -> AttackerView:
    """Filter raw calls according to the experiment's observable-view policy.

    Cached repeats are never independently exposed. Successful calls, empty
    results, and recognizable argument/application validation errors remain;
    transport, infrastructure, and loop-protection failures are removed.
    """

    visible: list[dict[str, Any]] = []
    removed_cached = 0
    removed_failed = 0
    retained_empty = 0
    retained_parameter_error = 0
    for call in tools_info:
        if not isinstance(call, dict):
            removed_failed += 1
            continue
        if _is_cached_duplicate(call):
            removed_cached += 1
            continue
        if _is_empty_result(call):
            retained_empty += 1
            visible.append(call)
            continue
        if _is_parameter_error(call):
            retained_parameter_error += 1
            visible.append(call)
            continue
        if _is_failed_call(call):
            removed_failed += 1
            continue
        visible.append(call)

    stats = AttackerViewStats(
        raw_call_count=len(tools_info),
        visible_call_count=len(visible),
        removed_cached_duplicate_count=removed_cached,
        removed_failed_call_count=removed_failed,
        retained_empty_result_count=retained_empty,
        retained_parameter_error_count=retained_parameter_error,
    )
    return AttackerView(calls=visible, stats=stats)


def prepare_raw_attacker_view(tools_info: list[dict[str, Any]]) -> AttackerView:
    """Expose every raw record exactly as the original attack baseline did."""

    calls = [call for call in tools_info if isinstance(call, dict)]
    stats = AttackerViewStats(
        raw_call_count=len(tools_info),
        visible_call_count=len(calls),
        removed_cached_duplicate_count=0,
        removed_failed_call_count=len(tools_info) - len(calls),
        retained_empty_result_count=sum(_is_empty_result(call) for call in calls),
        retained_parameter_error_count=sum(
            _is_parameter_error(call) for call in calls
        ),
    )
    return AttackerView(calls=calls, stats=stats)


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                value = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                return re.sub(r"\s+", " ", stripped)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _call_result_text(call: Mapping[str, Any]) -> str | None:
    value = call.get("returned_result", call.get("result"))
    if value in (None, "") and call.get("error") not in (None, ""):
        value = {"error": call.get("error")}
    if value in (None, ""):
        return None
    return _json_text(value)


def _tool_identity(call: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(call.get("server_name") or call.get("server") or "unknown"),
        str(call.get("tool_name") or call.get("tool") or "unknown"),
    )


def _render_observation(calls: list[dict[str, Any]]) -> str:
    catalog: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}
    for call in calls:
        identity = _tool_identity(call)
        if identity not in catalog:
            catalog[identity] = (f"T{len(catalog) + 1}", call)

    lines = ["## Tool catalog"]
    for (server_name, tool_name), (reference, call) in catalog.items():
        lines.append(f"--- {reference} ---")
        lines.append(f"Server: {server_name}")
        lines.append(f"Tool Name: {tool_name}")
        description = str(call.get("tool_description") or "")
        if description:
            lines.append(f"Tool Description: {description}")
        schema = call.get("tool_schema")
        if schema not in (None, {}, ""):
            lines.append(f"Tool Schema: {_json_text(schema)}")

    lines.append("")
    lines.append("## Ordered tool-call events")
    for index, call in enumerate(calls, start=1):
        reference = catalog[_tool_identity(call)][0]
        lines.append(f"--- Tool Call {index} ---")
        lines.append(f"Tool Reference: {reference}")
        statement = call.get("call_statement", call.get("parameters"))
        if statement is not None:
            lines.append(f"Tool Call Statement: {_json_text(statement)}")
        result_text = _call_result_text(call)
        if result_text is not None:
            lines.append(f"Tool Returned Result: {result_text}")
        lines.append(f"Call Order: {call.get('call_order', index)}")
        lines.append("")
    if not calls:
        lines.append("No observable tool calls.")
    return "\n".join(lines)


def format_attacker_observation(
    calls: list[dict[str, Any]],
) -> FormattedObservation:
    """Factor repeated tool metadata without dropping any observable value."""

    text = _render_observation(calls)
    tool_count = len({_tool_identity(call) for call in calls})
    stats = ObservationFormatStats(
        observation_chars=len(text),
        tool_catalog_count=tool_count,
    )
    return FormattedObservation(text=text, stats=stats)


def format_raw_repeated_observation(
    calls: list[dict[str, Any]],
) -> FormattedObservation:
    """Reproduce the original per-call repeated-metadata prompt representation."""

    lines: list[str] = []
    for index, call in enumerate(calls, start=1):
        lines.append(f"--- Tool Call {index} ---")
        lines.append(f"Tool Name: {call.get('tool_name', 'N/A')}")
        lines.append(f"Tool Description: {call.get('tool_description', 'N/A')}")
        schema = call.get("tool_schema")
        if schema is not None:
            lines.append(
                f"Tool Schema: {json.dumps(schema, ensure_ascii=False)}"
            )
        statement = call.get("call_statement")
        if statement is not None:
            lines.append(
                "Tool Call Statement: "
                f"{json.dumps(statement, ensure_ascii=False)}"
            )
        result = call.get("returned_result")
        if result is not None:
            lines.append(
                "Tool Returned Result: "
                f"{json.dumps(result, ensure_ascii=False)}"
            )
        lines.append(f"Call Order: {call.get('call_order', index)}")
        lines.append("")
    text = "\n".join(lines)
    return FormattedObservation(
        text=text,
        stats=ObservationFormatStats(
            observation_chars=len(text),
            tool_catalog_count=0,
        ),
    )


def prepare_attacker_view_for_format(
    tools_info: list[dict[str, Any]],
    observation_format: str = LOSSLESS_TOOL_CATALOG,
) -> AttackerView:
    if observation_format == LOSSLESS_TOOL_CATALOG:
        return prepare_attacker_view(tools_info)
    if observation_format == RAW_REPEATED:
        return prepare_raw_attacker_view(tools_info)
    raise ValueError(f"Unsupported attacker observation format: {observation_format}")


def build_attacker_observation(
    tools_info: list[dict[str, Any]],
    observation_format: str = LOSSLESS_TOOL_CATALOG,
) -> AttackerObservation:
    """Build either the original baseline or the lossless catalog treatment."""

    view = prepare_attacker_view_for_format(tools_info, observation_format)
    if observation_format == LOSSLESS_TOOL_CATALOG:
        formatted = format_attacker_observation(view.calls)
        view_policy = attacker_view_policy_for_format(observation_format)
    elif observation_format == RAW_REPEATED:
        formatted = format_raw_repeated_observation(view.calls)
        view_policy = attacker_view_policy_for_format(observation_format)
    else:
        raise ValueError(f"Unsupported attacker observation format: {observation_format}")
    return AttackerObservation(
        view=view,
        formatted=formatted,
        view_policy=view_policy,
        observation_format=observation_format,
    )
