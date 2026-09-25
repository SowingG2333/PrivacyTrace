"""Runtime limits for the Paper Search MCP server."""

from __future__ import annotations

import os


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    value = float(raw) if raw else default
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    value = int(raw) if raw else default
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


# Blocking search implementations are isolated in this bounded worker pool.
# Twelve workers let independent academic sources progress concurrently without
# allowing a 30-trajectory batch to create an unbounded number of HTTP calls.
SEARCH_MAX_WORKERS = _positive_int("PAPER_SEARCH_MAX_WORKERS", 12)

# A tool must return control well before the enclosing 300-second Agent turn
# deadline. Individual HTTP operations use a smaller timeout so executor threads
# also terminate promptly after a tool-level timeout.
SEARCH_TOOL_TIMEOUT_SECONDS = _positive_float(
    "PAPER_SEARCH_TOOL_TIMEOUT_SECONDS", 60.0
)
REQUEST_TIMEOUT_SECONDS = _positive_float(
    "PAPER_SEARCH_REQUEST_TIMEOUT_SECONDS", 20.0
)

# NCBI permits at most three requests per second without an API key. A PubMed
# search performs two requests, so serialize them at a conservative interval.
PUBMED_MIN_REQUEST_INTERVAL_SECONDS = _positive_float(
    "PAPER_SEARCH_PUBMED_MIN_REQUEST_INTERVAL_SECONDS", 0.36
)
