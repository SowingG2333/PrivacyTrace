"""Thread-safe, direct OpenAI-compatible client used by privacy experiments."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import threading
from typing import Any

from agent_env.recovery import RollingRateLimiter


@dataclass(frozen=True)
class LLMResponse:
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _KeyClient:
    """A selected credential slot. The credential is never exposed in metadata."""

    slot: int
    generation: int
    client: Any
    http_client: Any
    limiter: RollingRateLimiter | None
    model_override: str | None = None


def _split_values(raw: str) -> tuple[str, ...]:
    return tuple(value for value in re.split(r"[\s,;]+", raw.strip()) if value)


class DirectOpenAIClient:
    """RPM-weighted API keys with one isolated HTTP pool per credential."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_keys: tuple[str, ...] = (),
        key_rpms: tuple[int, ...] | None = None,
        key_slots: tuple[int, ...] = (),
        key_models: tuple[str, ...] | None = None,
        base_url: str,
        timeout: float = 600.0,
        max_connections: int = 100,
        trust_env: bool = False,
    ) -> None:
        import httpx
        from openai import OpenAI

        credentials = tuple(dict.fromkeys(key for key in (api_key, *api_keys) if key))
        if not credentials:
            raise RuntimeError("At least one OpenAI-compatible API key is required")
        if key_rpms is not None and len(key_rpms) != len(credentials):
            raise ValueError("key_rpms must provide one value per API key")
        if key_slots and len(key_slots) != len(credentials):
            raise ValueError("key_slots must provide one slot per API key")
        if key_models is not None and len(key_models) != len(credentials):
            raise ValueError("key_models must provide one model per API key")
        if key_rpms is not None and any(rpm < 0 for rpm in key_rpms):
            raise ValueError("key_rpms values must be non-negative")

        self._openai_type = OpenAI
        self._httpx = httpx
        self._credentials = credentials
        self._key_rpms = key_rpms
        self._key_slots = key_slots or tuple(range(1, len(credentials) + 1))
        self._key_models = key_models
        self._base_url = base_url
        self._timeout = timeout
        self._max_connections = max_connections
        self._trust_env = trust_env
        self._transport_lock = threading.RLock()
        self._slot_positions = {
            slot: position
            for position, slot in enumerate(self._key_slots)
            if self._key_rpms is None or self._key_rpms[position] > 0
        }
        self._enabled_slots = tuple(self._slot_positions)
        self._key_limiters = {
            slot: (
                RollingRateLimiter(self._key_rpms[position])
                if self._key_rpms is not None
                else None
            )
            for slot, position in self._slot_positions.items()
        }
        self._key_generations = {slot: 0 for slot in self._enabled_slots}
        self._key_clients: dict[int, _KeyClient] = {}
        self._closed = False
        for slot in self._enabled_slots:
            self._build_key_client(slot)
        if not self._enabled_slots:
            raise RuntimeError("No configured API key is enabled")
        self._selection_lock = threading.Lock()
        self._selection_weights = {
            slot: (
                self._key_rpms[position]
                if self._key_rpms is not None
                else 1
            )
            for slot, position in self._slot_positions.items()
        }
        self._selection_current_weights = {
            slot: 0 for slot in self._enabled_slots
        }
        self._selection_total_weight = sum(self._selection_weights.values())

    def _build_key_client(self, slot: int) -> _KeyClient:
        """Create an isolated transport generation for exactly one key slot."""
        position = self._slot_positions[slot]
        http_client = self._httpx.Client(
            limits=self._httpx.Limits(
                max_connections=self._max_connections,
                max_keepalive_connections=self._max_connections,
            ),
            timeout=self._httpx.Timeout(self._timeout),
            trust_env=self._trust_env,
        )
        self._key_generations[slot] += 1
        selected = _KeyClient(
            slot=slot,
            generation=self._key_generations[slot],
            client=self._openai_type(
                api_key=self._credentials[position],
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=0,
                http_client=http_client,
            ),
            http_client=http_client,
            limiter=self._key_limiters[slot],
            model_override=(
                self._key_models[position] if self._key_models is not None else None
            ),
        )
        self._key_clients[slot] = selected
        return selected

    @staticmethod
    def _close_key_client(key_client: _KeyClient) -> None:
        key_client.client.close()
        key_client.http_client.close()

    def _abort_key_client(self, generation: int, slot: int) -> None:
        """Hard-stop only the timed-out key without disrupting healthy slots."""
        with self._transport_lock:
            selected = self._key_clients.get(slot)
            if (
                self._closed
                or selected is None
                or generation != selected.generation
            ):
                return
            del self._key_clients[slot]
        self._close_key_client(selected)

    def _acquire_key_client(self) -> tuple[int, _KeyClient]:
        with self._transport_lock:
            if self._closed:
                raise RuntimeError("DirectOpenAIClient is closed")
            with self._selection_lock:
                # Smooth weighted round-robin keeps every configured key busy
                # in proportion to its independent RPM budget. A plain 1:1
                # rotation would make a 500/100 pool behave like two 100-RPM
                # keys because half the workers would block on the slower key.
                for candidate in self._enabled_slots:
                    self._selection_current_weights[candidate] += (
                        self._selection_weights[candidate]
                    )
                slot = max(
                    self._enabled_slots,
                    key=lambda candidate: self._selection_current_weights[candidate],
                )
                self._selection_current_weights[slot] -= (
                    self._selection_total_weight
                )
            selected = self._key_clients.get(slot)
            if selected is None:
                selected = self._build_key_client(slot)
        return selected.generation, selected

    @staticmethod
    def _environment_key_configuration() -> tuple[tuple[str, ...], tuple[int, ...] | None, tuple[int, ...]]:
        """Resolve selected slots without ever returning them in run metadata."""
        primary = os.environ.get("MY_MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY")
        extras = _split_values(os.environ.get("MY_MODEL_API_KEYS", ""))
        credentials = tuple(dict.fromkeys(key for key in (primary, *extras) if key))
        raw_rpms = os.environ.get("MY_MODEL_API_KEY_RPMS", "").strip()
        if raw_rpms:
            try:
                all_rpms = tuple(int(value) for value in _split_values(raw_rpms))
            except ValueError as exc:
                raise ValueError("MY_MODEL_API_KEY_RPMS must contain integers") from exc
            if len(all_rpms) != len(credentials) or any(rpm < 0 for rpm in all_rpms):
                raise ValueError(
                    "MY_MODEL_API_KEY_RPMS must provide one non-negative value per key"
                )
        else:
            all_rpms = None
        raw_indexes = os.environ.get("PRIVACY_ATTACK_API_KEY_INDEXES", "").strip()
        try:
            indexes = (
                tuple(int(value) for value in _split_values(raw_indexes))
                if raw_indexes
                else tuple(range(1, len(credentials) + 1))
            )
        except ValueError as exc:
            raise ValueError("PRIVACY_ATTACK_API_KEY_INDEXES must contain integers") from exc
        if (
            not indexes
            or len(set(indexes)) != len(indexes)
            or any(index < 1 or index > len(credentials) for index in indexes)
        ):
            raise ValueError(
                "PRIVACY_ATTACK_API_KEY_INDEXES must be unique 1-based configured key slots"
            )
        selected = tuple(credentials[index - 1] for index in indexes)
        selected_rpms = (
            tuple(all_rpms[index - 1] for index in indexes)
            if all_rpms is not None
            else None
        )
        return selected, selected_rpms, indexes

    @classmethod
    def environment_key_count(cls) -> int:
        """Return enabled configured credential slots without exposing credentials."""
        credentials, key_rpms, _ = cls._environment_key_configuration()
        return sum(
            rpm is None or rpm > 0
            for rpm in (key_rpms if key_rpms is not None else (None,) * len(credentials))
        )

    @classmethod
    def from_environment(
        cls, *, timeout: float = 600.0, max_connections: int = 100,
        use_environment_key_models: bool = False,
        trust_env: bool = False,
    ) -> "DirectOpenAIClient":
        credentials, key_rpms, key_slots = cls._environment_key_configuration()
        base_url = os.environ.get("MY_MODEL_BASE_URL")
        if not credentials or not base_url:
            raise RuntimeError(
                "MY_MODEL_BASE_URL and MY_MODEL_API_KEY or MY_MODEL_API_KEYS are required"
            )
        key_models = None
        if use_environment_key_models:
            all_models = _split_values(os.environ.get("MY_MODEL_API_KEY_MODELS", ""))
            primary = os.environ.get("MY_MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY")
            all_credentials = tuple(dict.fromkeys(key for key in (primary, *_split_values(os.environ.get("MY_MODEL_API_KEYS", ""))) if key))
            if len(all_models) != len(all_credentials):
                raise ValueError("MY_MODEL_API_KEY_MODELS must provide one model per configured key")
            key_models = tuple(all_models[slot - 1] for slot in key_slots)
        return cls(
            api_keys=credentials,
            key_rpms=key_rpms,
            key_slots=key_slots,
            key_models=key_models,
            base_url=base_url,
            timeout=timeout,
            max_connections=max_connections,
            trust_env=trust_env,
        )

    @property
    def key_count(self) -> int:
        return len(self._enabled_slots)

    def complete_json(
        self,
        *,
        prompt: str,
        model: str,
        temperature: float,
        stage: str,
        limiter: RollingRateLimiter,
        thinking_mode: str | None = None,
    ) -> LLMResponse:
        """Issue exactly one request and deliberately never send ``max_tokens``."""
        # The caller limiter caps the whole experiment. Each selected key also
        # observes its configured independent cap before the real API request.
        limiter.acquire()
        _, key_client = self._acquire_key_client()
        if key_client.limiter is not None:
            key_client.limiter.acquire()
        request_model = key_client.model_override or model
        kwargs: dict[str, Any] = {
            "model": request_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if thinking_mode == "disabled":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        # httpx already applies this timeout to each request. Closing a shared
        # per-key pool from a separate timer would also terminate every other
        # healthy request using that credential and create connection-error
        # cascades under high concurrency.
        response = key_client.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message
        # Some OpenAI-compatible deployments (notably the configured glm5-2
        # endpoint with thinking disabled) return their final JSON in
        # ``message.reasoning`` rather than ``message.content``.  Prefer the
        # standard field, but preserve that compatible final-output field so
        # callers can apply their own JSON/schema validation.
        response_text = (
            getattr(message, "content", None)
            or getattr(message, "reasoning", None)
            or getattr(message, "reasoning_content", None)
            or ""
        )
        response_field = (
            "content"
            if getattr(message, "content", None)
            else (
                "reasoning"
                if getattr(message, "reasoning", None)
                else (
                    "reasoning_content"
                    if getattr(message, "reasoning_content", None)
                    else "empty"
                )
            )
        )
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=response_text,
            metadata={
                "stage": stage,
                "model": request_model,
                "logical_model": model,
                "response_model": getattr(response, "model", None),
                "response_id": getattr(response, "id", None),
                "system_fingerprint": getattr(response, "system_fingerprint", None),
                "finish_reason": getattr(choice, "finish_reason", None),
                "response_content_field": response_field,
                "thinking_mode": thinking_mode or "provider_default",
                "prompt_chars": len(prompt),
                "output_limit_source": "service_default",
                "max_tokens": None,
                "direct_transport": (
                    "trust_env_true" if self._trust_env else "trust_env_false"
                ),
                "timeout_seconds": self._timeout,
                "timeout_enforcement": "httpx_per_request_timeout",
                "key_slot": key_client.slot,
                "key_pool_size": self.key_count,
                "usage": {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(usage, "completion_tokens", 0),
                    "total_tokens": getattr(usage, "total_tokens", 0),
                }
                if usage is not None
                else {},
            },
        )

    def close(self) -> None:
        with self._transport_lock:
            self._closed = True
            key_clients = tuple(self._key_clients.values())
            self._key_clients = {}
        for key_client in key_clients:
            self._close_key_client(key_client)
