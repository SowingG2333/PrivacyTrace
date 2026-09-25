#!/usr/bin/env python3
"""Keep a resumable privacy runner moving by refreshing stalled processes.

The attack runners checkpoint each completed view atomically. This supervisor
can therefore restart a child command after a provider connection makes no
observable progress, without discarding completed work. Each restart creates a
fresh direct HTTP connection pool.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Sequence


class _StopSupervision(Exception):
    """Request a clean watchdog shutdown after receiving a process signal."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def output_count(output_dir: Path, pattern: str) -> int:
    """Return the number of materialized result files for the supervised arm."""
    if not output_dir.exists():
        return 0
    return sum(1 for path in output_dir.glob(pattern) if path.is_file())


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--expected-count", type=int, required=True)
    value.add_argument("--pattern", default="S*.json")
    value.add_argument("--stall-seconds", type=float, default=150.0)
    value.add_argument("--poll-seconds", type=float, default=5.0)
    value.add_argument("--restart-delay", type=float, default=2.0)
    value.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Runner invocation following '--'; it must include --resume.",
    )
    return value


def _validate(args: argparse.Namespace) -> list[str]:
    if args.expected_count < 0:
        raise ValueError("--expected-count must be non-negative")
    if args.stall_seconds <= 0 or args.poll_seconds <= 0 or args.restart_delay < 0:
        raise ValueError("watchdog intervals must be positive (restart delay may be zero)")
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise ValueError("provide the resumable runner command after '--'")
    if "--resume" not in command:
        raise ValueError("the supervised command must include --resume")
    return command


def _terminate(child: subprocess.Popen[object], grace_seconds: float = 10.0) -> None:
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except (AttributeError, ProcessLookupError):
        child.terminate()
    try:
        child.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (AttributeError, ProcessLookupError):
        child.kill()
    child.wait()


def _write_status(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def supervise(args: argparse.Namespace) -> int:
    command = _validate(args)
    output_dir: Path = args.output_dir
    status_path = output_dir.parent / "_connection_watchdog.json"
    refreshes = 0
    child: subprocess.Popen[object] | None = None
    last_count = output_count(output_dir, args.pattern)
    last_progress = time.monotonic()

    watched_signals = tuple(
        candidate
        for candidate in (
            getattr(signal, "SIGHUP", None),
            getattr(signal, "SIGINT", None),
            getattr(signal, "SIGTERM", None),
        )
        if candidate is not None
    )
    previous_handlers = {
        candidate: signal.getsignal(candidate) for candidate in watched_signals
    }

    def request_stop(signum: int, _frame: object) -> None:
        raise _StopSupervision(signum)

    for candidate in watched_signals:
        signal.signal(candidate, request_stop)

    stopped_by_signal: int | None = None
    try:
        while last_count < args.expected_count:
            if child is None:
                print(
                    f"[watchdog] starting runner; completed={last_count}/{args.expected_count}",
                    flush=True,
                )
                child = subprocess.Popen(command, start_new_session=True)
                last_progress = time.monotonic()

            time.sleep(args.poll_seconds)
            current_count = output_count(output_dir, args.pattern)
            now = time.monotonic()
            if current_count > last_count:
                last_count = current_count
                last_progress = now
                print(f"[watchdog] progress {last_count}/{args.expected_count}", flush=True)

            state: dict[str, object] = {
                "output_dir": str(output_dir),
                "expected_count": args.expected_count,
                "completed_count": last_count,
                "refreshes": refreshes,
                "child_pid": child.pid if child is not None else None,
                "transport": "direct_no_proxy",
                "connection_refresh": "fresh_process_and_http_pool_on_stall",
                "updated_at_unix": time.time(),
            }
            _write_status(status_path, state)

            if current_count >= args.expected_count:
                # A runner writes the final checkpoint before resolving its
                # failure-ledger entry and committing its completion manifest.
                # Give that bookkeeping a short grace period instead of
                # killing the child immediately from ``finally``.
                try:
                    child.wait(timeout=30.0)
                except subprocess.TimeoutExpired:
                    print(
                        "[watchdog] all outputs materialized but runner did not "
                        "finish bookkeeping within 30s; stopping it",
                        flush=True,
                    )
                    _terminate(child)
                child = None
                break
            if child.poll() is not None:
                code = child.returncode
                print(f"[watchdog] runner exited ({code}); resuming", flush=True)
                child = None
                time.sleep(args.restart_delay)
                continue
            if now - last_progress >= args.stall_seconds:
                refreshes += 1
                print(
                    f"[watchdog] no completed view for {args.stall_seconds:.0f}s; "
                    f"refreshing direct connection pool (restart {refreshes})",
                    flush=True,
                )
                _terminate(child)
                child = None
                time.sleep(args.restart_delay)
    except _StopSupervision as exc:
        stopped_by_signal = exc.signum
        print(f"[watchdog] received signal {exc.signum}; stopping runner", flush=True)
    finally:
        if child is not None and child.poll() is None:
            _terminate(child)
        for candidate, previous_handler in previous_handlers.items():
            signal.signal(candidate, previous_handler)

    if stopped_by_signal is not None:
        _write_status(
            status_path,
            {
                "output_dir": str(output_dir),
                "expected_count": args.expected_count,
                "completed_count": output_count(output_dir, args.pattern),
                "refreshes": refreshes,
                "complete": False,
                "stopped_by_signal": stopped_by_signal,
                "updated_at_unix": time.time(),
            },
        )
        return 128 + stopped_by_signal

    _write_status(
        status_path,
        {
            "output_dir": str(output_dir),
            "expected_count": args.expected_count,
            "completed_count": output_count(output_dir, args.pattern),
            "refreshes": refreshes,
            "complete": True,
            "transport": "direct_no_proxy",
            "connection_refresh": "fresh_process_and_http_pool_on_stall",
            "updated_at_unix": time.time(),
        },
    )
    print(f"[watchdog] complete {args.expected_count}/{args.expected_count}", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return supervise(parser().parse_args(argv))
    except ValueError as exc:
        parser().error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
