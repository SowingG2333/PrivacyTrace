"""Direct multi-key OpenAI-compatible transport with cross-process RPM pacing.

The trajectory runner executes cases in isolated subprocesses.  This module
keeps those subprocesses on the upstream API directly (no HTTP proxy) while
sharing anonymous per-key pacing state through a small locked file.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
from pathlib import Path
import re
import time
from typing import Any

import httpx


POOL_SCHEMA_VERSION = "1.0"


def _split(raw: str) -> tuple[str, ...]:
    return tuple(value for value in re.split(r"[\s,;]+", raw.strip()) if value)


def _configured_keys() -> tuple[str, ...]:
    primary = os.environ.get("MY_MODEL_API_KEY", "").strip()
    extras = _split(os.environ.get("MY_MODEL_API_KEYS", ""))
    return tuple(dict.fromkeys(value for value in (primary, *extras) if value))


def _selected_pool() -> tuple[tuple[str, ...], tuple[int, ...]] | None:
    if os.environ.get("TRAJECTORY_DIRECT_KEY_POOL", "").strip() != "1":
        return None
    all_keys = _configured_keys()
    if not all_keys:
        raise ValueError("Trajectory direct key pool has no configured API keys")

    raw_indexes = os.environ.get("TRAJECTORY_API_KEY_INDEXES", "1,2")
    try:
        indexes = tuple(int(value) for value in _split(raw_indexes))
    except ValueError as exc:
        raise ValueError("TRAJECTORY_API_KEY_INDEXES must contain integers") from exc
    if (
        not indexes
        or len(set(indexes)) != len(indexes)
        or any(index < 1 or index > len(all_keys) for index in indexes)
    ):
        raise ValueError(
            "TRAJECTORY_API_KEY_INDEXES must be unique 1-based configured key slots"
        )

    raw_rpms = os.environ.get("TRAJECTORY_API_KEY_RPMS", "").strip()
    if raw_rpms:
        try:
            rpms = tuple(int(value) for value in _split(raw_rpms))
        except ValueError as exc:
            raise ValueError("TRAJECTORY_API_KEY_RPMS must contain integers") from exc
    else:
        raw_all_rpms = os.environ.get("MY_MODEL_API_KEY_RPMS", "").strip()
        try:
            all_rpms = tuple(int(value) for value in _split(raw_all_rpms))
        except ValueError as exc:
            raise ValueError("MY_MODEL_API_KEY_RPMS must contain integers") from exc
        if len(all_rpms) != len(all_keys):
            raise ValueError(
                "MY_MODEL_API_KEY_RPMS must provide one value per configured key"
            )
        rpms = tuple(all_rpms[index - 1] for index in indexes)
    if len(rpms) != len(indexes) or any(value <= 0 for value in rpms):
        raise ValueError(
            "TRAJECTORY_API_KEY_RPMS must provide one positive value per selected key"
        )
    return tuple(all_keys[index - 1] for index in indexes), rpms


def direct_pool_audit() -> dict[str, Any] | None:
    selected = _selected_pool()
    if selected is None:
        return None
    keys, rpms = selected
    return {
        "transport": "direct",
        "proxy_enabled": False,
        "key_count": len(keys),
        "key_rpms": list(rpms),
        "provider_model": os.environ.get(
            "TRAJECTORY_PROVIDER_MODEL", ""
        ).strip(),
        "max_in_flight": int(
            os.environ.get("TRAJECTORY_MAX_IN_FLIGHT", "100")
        ),
    }


class DirectKeyPoolTransport(httpx.AsyncBaseTransport):
    """Rewrite auth per request after reserving a direct upstream key slot."""

    def __init__(
        self,
        *,
        keys: tuple[str, ...],
        rpms: tuple[int, ...],
        state_path: Path,
        max_connections: int,
    ) -> None:
        self.keys = keys
        self.rpms = rpms
        self.state_path = state_path.resolve()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_connections,
        )
        # Supplying an explicit transport and trust_env=False on the client
        # guarantees direct upstream connections without system proxy lookup.
        self._transport = httpx.AsyncHTTPTransport(limits=limits, retries=0)

    def _try_reserve(self) -> tuple[int | None, float]:
        now = time.time()
        with self.state_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read().strip()
            if raw:
                try:
                    state = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid trajectory rate state: {self.state_path}"
                    ) from exc
                if (
                    state.get("schema_version") != POOL_SCHEMA_VERSION
                    or state.get("key_rpms") != list(self.rpms)
                ):
                    raise ValueError(
                        "Trajectory rate state is incompatible with the direct key pool"
                    )
            else:
                state = {
                    "schema_version": POOL_SCHEMA_VERSION,
                    "transport": "direct",
                    "proxy_enabled": False,
                    "key_rpms": list(self.rpms),
                    "next_request_at": [0.0] * len(self.keys),
                    "request_counts": [0] * len(self.keys),
                    "created_at": now,
                }

            ready = [float(value) for value in state["next_request_at"]]
            index = min(range(len(ready)), key=ready.__getitem__)
            if ready[index] > now:
                return None, max(0.001, ready[index] - now)

            ready[index] = max(now, ready[index]) + 60.0 / self.rpms[index]
            counts = [int(value) for value in state["request_counts"]]
            counts[index] += 1
            state["next_request_at"] = ready
            state["request_counts"] = counts
            state["updated_at"] = now
            handle.seek(0)
            handle.truncate()
            json.dump(state, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
            return index, 0.0

    async def _reserve(self) -> int:
        while True:
            index, delay = await asyncio.to_thread(self._try_reserve)
            if index is not None:
                return index
            await asyncio.sleep(delay)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        index = await self._reserve()
        request.headers["authorization"] = f"Bearer {self.keys[index]}"
        request.headers.pop("proxy-authorization", None)
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


def build_direct_pool_http_client() -> httpx.AsyncClient | None:
    selected = _selected_pool()
    if selected is None:
        return None
    keys, rpms = selected
    raw_state_path = os.environ.get("TRAJECTORY_RATE_STATE", "").strip()
    if not raw_state_path:
        raise ValueError("TRAJECTORY_RATE_STATE is required for the direct key pool")
    max_in_flight = int(os.environ.get("TRAJECTORY_MAX_IN_FLIGHT", "100"))
    if max_in_flight <= 0:
        raise ValueError("TRAJECTORY_MAX_IN_FLIGHT must be positive")
    transport = DirectKeyPoolTransport(
        keys=keys,
        rpms=rpms,
        state_path=Path(raw_state_path),
        max_connections=max_in_flight,
    )
    return httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        timeout=httpx.Timeout(300.0),
    )
