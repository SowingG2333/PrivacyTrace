"""Shared crash-safe persistence and resumable-run bookkeeping.

The helpers in this module deliberately keep concurrency outside the immutable
run contract. A recovery invocation may lower its worker count without
invalidating otherwise compatible artifacts.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Callable, Iterable, Mapping


RECOVERY_SCHEMA_VERSION = "1.0"
REQUIRED_MODEL = "glm-5.2"


class ProviderHealthExceeded(RuntimeError):
    """Signal the orchestrator to resume a stage at lower concurrency."""


class FatalProviderError(RuntimeError):
    """Authentication/authorization/balance failure that must not be retried."""


def is_transient_provider_error(error: BaseException | str) -> bool:
    message = str(error).casefold()
    return bool(re.search(r"(?:error|status) code:\s*5\d\d", message)) or any(
        marker in message
        for marker in (
            "error code: 429",
            "status code: 429",
            "error code: 503",
            "status code: 503",
            "error code: 500",
            "status code: 500",
            "error code: 501",
            "status code: 501",
            "error code: 502",
            "status code: 502",
            "error code: 504",
            "status code: 504",
            "connection error",
            "connection reset",
            "connecterror",
            "connect timeout",
            "read timeout",
            "timed out",
            "timeout",
        )
    )


def is_fatal_provider_error(error: BaseException | str) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "error code: 401",
            "status code: 401",
            "error code: 402",
            "status code: 402",
            "error code: 403",
            "status code: 403",
            "insufficient balance",
            "insufficient_balance",
        )
    )


def is_nonretryable_request_error(error: BaseException | str) -> bool:
    """Return whether an error is a deterministic bad-input/context failure.

    JSON decoding and schema-validation failures are intentionally *not* listed
    here: privacy runners retry those because a model may emit malformed output
    transiently. This helper only identifies failures that cannot change unless
    the request itself changes.
    """
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "context length",
            "context window",
            "maximum context",
            "max context",
            "input is too long",
            "prompt is too long",
            "context overflow",
            "trajectory does not contain a tools_info array",
            "trajectory has no tools_info array",
            "expected exactly one trajectory for every domain",
        )
    )


class RollingFailureWindow:
    """Thread-safe recent-request health gate used to trigger a resume."""

    def __init__(
        self,
        *,
        window_size: int = 50,
        threshold: float = 0.05,
        min_samples: int | None = None,
    ) -> None:
        self.window_size = window_size
        self.threshold = threshold
        self.min_samples = window_size if min_samples is None else min_samples
        self._values: deque[bool] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record(self, failed: bool) -> bool:
        with self._lock:
            self._values.append(bool(failed))
            return (
                len(self._values) >= self.min_samples
                and sum(self._values) / len(self._values) > self.threshold
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            count = len(self._values)
            failures = sum(self._values)
        return {
            "sample_count": count,
            "failure_count": failures,
            "failure_rate": failures / count if count else 0.0,
            "threshold": self.threshold,
            "tripped": count >= self.min_samples
            and failures / count > self.threshold,
        }


class RollingRateLimiter:
    """Evenly space requests, or leave them uncapped when RPM is zero."""

    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute < 0:
            raise ValueError("requests_per_minute must be non-negative")
        self.interval = (
            0.0 if requests_per_minute == 0 else 60.0 / requests_per_minute
        )
        self._next_request = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.interval == 0.0:
            return
        with self._lock:
            now = time.monotonic()
            reserved = max(now, self._next_request)
            self._next_request = reserved + self.interval
        delay = reserved - now
        if delay > 0:
            time.sleep(delay)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def portable_path(path: Path, *, relative_to: Path | None = None) -> str:
    """Return a stable, relocatable path for persisted metadata.

    Runtime code may still resolve paths for I/O, but manifests and recovery
    ledgers must not encode the checkout's machine-specific absolute prefix.
    """
    base = (relative_to or Path.cwd()).resolve()
    return Path(os.path.relpath(path.resolve(), base)).as_posix()


def file_hashes(
    paths: Iterable[Path], *, relative_to: Path | None = None
) -> dict[str, str]:
    return {
        portable_path(path, relative_to=relative_to): sha256_file(path.resolve())
        for path in paths
        if path.is_file()
    }


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def atomic_write_json(path: Path, value: Any, *, indent: int = 2) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=indent) + "\n",
    )


def atomic_write_jsonl(
    path: Path, records: Iterable[Mapping[str, Any]]
) -> None:
    atomic_write_text(
        path,
        "".join(
            json.dumps(dict(record), ensure_ascii=False) + "\n"
            for record in records
        ),
    )


def append_jsonl_fsync(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            records.append(value)
    return records


def require_model(
    model: str,
    *,
    stage: str,
    expected_model: str = REQUIRED_MODEL,
) -> str:
    normalized = str(model or "").strip()
    expected = str(expected_model or "").strip()
    if not expected:
        raise ValueError(f"{stage} expected model must be non-empty")
    if normalized != expected:
        raise ValueError(
            f"{stage} must use {expected}; configured model is "
            f"{normalized or '<missing>'}"
        )
    return normalized


def require_nonempty_model(model: str, *, stage: str) -> str:
    """Validate a model identifier without pinning a provider-specific name."""
    normalized = str(model or "").strip()
    if not normalized:
        raise ValueError(f"{stage} model must be non-empty")
    return normalized


def quarantine_artifact(
    path: Path,
    quarantine_root: Path,
    *,
    item_id: str,
    reason: str,
) -> Path | None:
    if not path.exists():
        return None
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    digest = hashlib.sha256(
        f"{path.resolve()}:{time.time_ns()}:{reason}".encode("utf-8")
    ).hexdigest()[:10]
    destination = (
        quarantine_root
        / item_id
        / f"{timestamp}-{digest}-{path.name}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(path, destination)
    except OSError:
        shutil.copy2(path, destination)
        path.unlink()
    _fsync_directory(destination.parent)
    return destination


def validate_or_quarantine(
    path: Path,
    validator: Callable[[Path], bool],
    quarantine_root: Path,
    *,
    item_id: str,
    reason: str = "artifact_invalid",
) -> bool:
    if not path.exists():
        return False
    try:
        usable = bool(validator(path))
    except Exception:
        usable = False
    if usable:
        return True
    quarantine_artifact(
        path,
        quarantine_root,
        item_id=item_id,
        reason=reason,
    )
    return False


@dataclass(frozen=True)
class RecoveryContract:
    run_id: str
    phase: str
    model: str
    prompt_version: str
    seed: int | None
    input_hashes: Mapping[str, str]
    config: Mapping[str, Any]
    schema_version: str = RECOVERY_SCHEMA_VERSION
    required_model: str | None = REQUIRED_MODEL

    def immutable_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "phase": self.phase,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "seed": self.seed,
            "input_hashes": dict(sorted(self.input_hashes.items())),
            "config": dict(self.config),
        }

    @property
    def signature(self) -> str:
        return sha256_json(self.immutable_payload())


def ensure_recovery_manifest(
    path: Path,
    contract: RecoveryContract,
    *,
    workers: int,
    resume: bool,
    compatible_contract_signatures: Iterable[str] = (),
) -> dict[str, Any]:
    if contract.required_model is None:
        require_nonempty_model(contract.model, stage=contract.phase)
    else:
        require_model(
            contract.model,
            stage=contract.phase,
            expected_model=contract.required_model,
        )
    existing = read_json_object(path)
    if existing is not None:
        existing_signature = existing.get("contract_signature")
        if existing_signature != contract.signature:
            compatible = (
                resume
                and existing_signature in set(compatible_contract_signatures)
            )
            if resume and not compatible:
                raise ValueError(
                    f"Recovery manifest is incompatible for {contract.phase}: "
                    f"expected {contract.signature}, found {existing_signature}"
                )
            if not resume:
                raise ValueError(
                    f"Existing run data belongs to another {contract.phase} contract; "
                    "use a new run directory"
                )
    manifest = {
        **contract.immutable_payload(),
        "contract_signature": contract.signature,
        "workers": workers,
        "attempt_count": int((existing or {}).get("attempt_count", 0)) + 1,
        "started_at": (existing or {}).get("started_at", time.time()),
        "last_started_at": time.time(),
        "updated_at": time.time(),
        "status": "running",
    }
    atomic_write_json(path, manifest)
    return manifest


class FailureLedger:
    """Append-only failure history plus an atomic unresolved snapshot."""

    def __init__(self, root: Path, *, phase: str, run_id: str) -> None:
        self.root = root
        self.phase = phase
        self.run_id = run_id
        self.history_path = root / "failure_history.jsonl"
        self.unresolved_path = root / "unresolved_failures.jsonl"
        self._lock = threading.Lock()
        self._unresolved = {
            str(item.get("item_id")): item
            for item in read_jsonl(self.unresolved_path)
            if item.get("item_id")
        }

    def recovery_round(self, item_id: str, *, resume: bool) -> int:
        previous = self._unresolved.get(item_id)
        if not resume or previous is None:
            return 0
        return int(previous.get("recovery_round", 0)) + 1

    def can_attempt(
        self, item_id: str, *, resume: bool, max_recovery_rounds: int
    ) -> bool:
        return self.recovery_round(item_id, resume=resume) <= max_recovery_rounds

    def record_failure(
        self,
        item_id: str,
        *,
        error_type: str,
        error: str,
        attempts: int,
        recovery_round: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = time.time()
        record = {
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "run_id": self.run_id,
            "phase": self.phase,
            "item_id": item_id,
            "status": "unresolved",
            "error_type": error_type,
            "error": error,
            "attempts": attempts,
            "recovery_round": recovery_round,
            "updated_at": timestamp,
            **dict(metadata or {}),
        }
        with self._lock:
            append_jsonl_fsync(self.history_path, record)
            self._unresolved[item_id] = record
            atomic_write_jsonl(
                self.unresolved_path,
                [self._unresolved[key] for key in sorted(self._unresolved)],
            )
        return record

    def resolve(
        self,
        item_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock:
            previous = self._unresolved.pop(item_id, None)
            if previous is None:
                # Resume scans call resolve for every already-valid checkpoint.
                # With no unresolved record there is no state transition, so
                # rewriting the snapshot would be pure (and costly) I/O.
                return
            append_jsonl_fsync(
                self.history_path,
                {
                    **previous,
                    "status": "resolved",
                    "resolved_at": time.time(),
                    **dict(metadata or {}),
                },
            )
            atomic_write_jsonl(
                self.unresolved_path,
                [self._unresolved[key] for key in sorted(self._unresolved)],
            )

    def unresolved(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(self._unresolved[key]) for key in sorted(self._unresolved)
            ]
