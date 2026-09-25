"""Classify MCP responses by transport and semantic content quality."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping


ERROR_MARKERS = (
    "circuitbreakererror",
    "circuit breaker",
    "client error: forbidden",
    "403 forbidden",
    "max retry attempts",
    "maximum retry attempts",
    "max retries exceeded",
    "connection refused",
    "connection closed",
    "request timed out",
    "read timed out",
    "tool execution failed",
    "api request failed",
    "bad gateway",
    "service unavailable",
    "too many requests",
    "rate limit",
    "an error occurred",
    "error retrieving",
    "error searching",
)

EMPTY_TEXT_PATTERNS = (
    re.compile(r"(?:results?|items?|matches?|locations?|documents?|hits)\s*[:=]\s*\[\s*\]\s*$", re.I),
    re.compile(r"^location\s*:\s*\{.*\}\s*\[\s*\]\s*$", re.I | re.S),
)


@dataclass(frozen=True)
class ToolOutcome:
    kind: str
    usable: bool
    reason: str | None = None


def extract_tool_text(value: Any) -> Any:
    """Extract text from MCP CallToolResult-like objects without SDK imports."""
    content = getattr(value, "content", None)
    if content:
        parts = [
            str(item.text)
            for item in content
            if getattr(item, "text", None) is not None
        ]
        if parts:
            return "".join(parts)
    return value


def _looks_like_empty_text(value: str) -> bool:
    normalized = value.strip()
    return any(pattern.search(normalized) for pattern in EMPTY_TEXT_PATTERNS)


def _json_has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and not _looks_like_empty_text(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value) and any(_json_has_content(item) for item in value)
    if isinstance(value, Mapping):
        if value.get("error"):
            return False
        content_keys = ("results", "items", "data", "content", "documents", "hits")
        present = [key for key in content_keys if key in value]
        if present:
            return any(_json_has_content(value[key]) for key in present)
        return bool(value)
    return True


def classify_tool_output(value: Any, transport_error: bool = False) -> ToolOutcome:
    """Classify transport success separately from useful application content."""
    if transport_error:
        return ToolOutcome("transport_error", False, "MCP transport reported an error")

    value = extract_tool_text(value)
    if value is None:
        return ToolOutcome("empty", False, "Tool returned no content")

    parsed: Any = value if isinstance(value, (Mapping, list, tuple)) else None
    normalized = str(value).strip()
    if not normalized or normalized.lower() in {"none", "null", "[]", "{}"}:
        return ToolOutcome("empty", False, "Tool returned no usable content")

    lowered = normalized.lower()
    if any(marker in lowered for marker in ERROR_MARKERS):
        return ToolOutcome("application_error", False, "Tool returned an error payload")

    if parsed is None:
        try:
            parsed = json.loads(normalized)
        except (json.JSONDecodeError, TypeError):
            parsed = None
    if parsed is not None and not _json_has_content(parsed):
        if isinstance(parsed, Mapping) and parsed.get("error"):
            return ToolOutcome("application_error", False, "Tool returned an error object")
        return ToolOutcome("empty", False, "Tool returned an empty result set")
    if _looks_like_empty_text(normalized):
        return ToolOutcome("empty", False, "Tool returned an empty result set")
    return ToolOutcome("success", True)


def tool_call_signature(tool: str, parameters: Mapping[str, Any] | None) -> str:
    """Return a stable signature for duplicate-call detection."""
    payload = json.dumps(
        {"tool": tool, "parameters": parameters or {}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()
