#!/usr/bin/env python3
"""Run multiple agent env cases, isolating each MCP session in a process."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterator

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
TRAJECTORY_MODEL = "deepseek-v4-flash"
DEFAULT_PROFILES = (
    PROJECT_DIR
    / "artifacts/profile_pool/profiles.jsonl"
)
DEFAULT_SCENARIOS = (
    PROJECT_DIR
    / "artifacts/scenario/cases.jsonl"
)
sys.path.insert(0, str(PROJECT_DIR))

from agent_env.recovery import (  # noqa: E402
    FailureLedger,
    RecoveryContract,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    ensure_recovery_manifest,
    file_hashes,
    quarantine_artifact,
    require_model,
)
from agent_env.settings import load_project_env  # noqa: E402


@contextmanager
def termination_signal_handlers() -> Iterator[None]:
    """Translate SIGINT/SIGTERM into the batch's normal interruption path."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    handled_signals = [signal.SIGINT]
    if hasattr(signal, "SIGTERM"):
        handled_signals.append(signal.SIGTERM)
    previous = {signum: signal.getsignal(signum) for signum in handled_signals}

    def interrupt_batch(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt(f"batch interrupted by signal {signum}")

    try:
        for signum in handled_signals:
            signal.signal(signum, interrupt_batch)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def mark_recovery_manifest_interrupted(
    path: Path,
    *,
    unresolved_failure_count: int | None = None,
) -> None:
    """Atomically mark an active recovery manifest as interrupted."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    if not isinstance(manifest, dict) or manifest.get("status") != "running":
        return
    interrupted_at = time.time()
    manifest.update(
        {
            "status": "interrupted",
            "updated_at": interrupted_at,
            "interrupted_at": interrupted_at,
        }
    )
    if unresolved_failure_count is not None:
        manifest["unresolved_failure_count"] = unresolved_failure_count
    atomic_write_json(path, manifest)


def read_scenarios(path: Path) -> list[dict[str, Any]]:
    from agent_env.case_loader import read_jsonl

    records = read_jsonl(path)
    for line_number, record in enumerate(records, start=1):
        if not record.get("scenario_id"):
            raise ValueError(f"Invalid scenario record on line {line_number}")
    return records


def select_cases(
    records: list[dict[str, Any]], args: argparse.Namespace
) -> list[dict[str, Any]]:
    selected = records
    if args.scenario_ids:
        wanted = set(args.scenario_ids)
        selected = [item for item in selected if item["scenario_id"] in wanted]
        missing = wanted - {item["scenario_id"] for item in selected}
        if missing:
            raise ValueError(f"Unknown scenario ids: {sorted(missing)}")
    if args.domains:
        selected = [item for item in selected if item.get("domain") in set(args.domains)]
    if getattr(args, "one_per_profile", False):
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in selected:
            profile_key = str(item.get("profile_id") or item["scenario_id"])
            grouped.setdefault(profile_key, []).append(item)
        # Rotate through each profile's available domains. With the canonical
        # four-domain dataset this yields 250 trajectories per domain while
        # covering every profile exactly once.
        selected = [
            candidates[index % len(candidates)]
            for index, candidates in enumerate(grouped.values())
        ]
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("limit must be positive")
        selected = selected[: args.limit]
    if not selected:
        raise ValueError("No scenarios selected")
    return selected


def write_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def assess_trajectory(
    path: Path,
    required_servers: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Classify an artifact independently from the child process exit code."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "usable": False,
            "failure_type": "infrastructure",
            "failure_reason": "artifact_missing",
        }
    except (OSError, json.JSONDecodeError):
        return {
            "usable": False,
            "failure_type": "infrastructure",
            "failure_reason": "artifact_invalid",
        }
    if not isinstance(value, dict):
        return {
            "usable": False,
            "failure_type": "infrastructure",
            "failure_reason": "artifact_invalid",
        }

    status = value.get("status")
    tools_info = value.get("tools_info")
    if not isinstance(tools_info, list):
        return {
            "usable": False,
            "failure_type": "infrastructure",
            "failure_reason": "artifact_missing_tools_info",
            "trajectory_status": status,
        }

    connections = value.get("server_connections")
    if not isinstance(connections, dict):
        return {
            "usable": False,
            "failure_type": "infrastructure",
            "failure_reason": "server_connections_missing",
            "trajectory_status": status,
        }

    configured = [
        str(name) for name in connections.get("configured_servers", [])
    ]
    connected = {
        str(name) for name in connections.get("connected_servers", [])
    }
    failed = {
        str(name) for name in connections.get("failed_servers", [])
    }
    policy = value.get("environment", {}).get("server_policy", {})
    policy_required = (
        policy.get("required_servers", []) if isinstance(policy, dict) else []
    )
    explicit_required = required_servers is not None or bool(policy_required)
    required = list(
        dict.fromkeys(
            required_servers if required_servers is not None else policy_required
        )
    )

    if explicit_required:
        failed_required = [name for name in required if name not in connected]
    else:
        # Legacy artifacts did not persist server policy, so all configured
        # servers remain required unless the caller supplies the domain policy.
        failed_required = sorted(failed)
        configured_count = int(connections.get("configured_count", 0) or 0)
        connected_count = int(connections.get("connected_count", 0) or 0)
        if (
            not failed_required
            and configured_count > 0
            and connected_count != configured_count
        ):
            failed_required = ["<unknown>"]
        if not configured and configured_count <= 0:
            return {
                "usable": False,
                "failure_type": "infrastructure",
                "failure_reason": "no_mcp_servers_configured",
                "trajectory_status": status,
            }
    failed_optional = sorted(failed - set(failed_required))
    connection_details = {
        "required_servers": required or configured,
        "failed_required_servers": failed_required,
        "failed_optional_servers": failed_optional,
    }
    if failed_required:
        return {
            "usable": False,
            "failure_type": "infrastructure",
            "failure_reason": "required_server_unavailable",
            "trajectory_status": status,
            **connection_details,
        }
    if status == "environment_error":
        return {
            "usable": False,
            "failure_type": "infrastructure",
            "failure_reason": "environment_error",
            "trajectory_status": status,
            **connection_details,
        }

    evaluation = value.get("evaluation")
    return {
        "usable": True,
        "failure_type": None,
        "failure_reason": None,
        "trajectory_status": status,
        "evaluator_passed": (
            evaluation.get("passed") is True
            if isinstance(evaluation, dict)
            else status == "satisfied"
        ),
        **connection_details,
    }


def has_usable_trajectory(
    path: Path,
    required_servers: list[str] | tuple[str, ...] | None = None,
) -> bool:
    """Return whether an existing artifact is safe to skip during resume."""
    return bool(assess_trajectory(path, required_servers).get("usable"))


def build_failure_records(
    results: list[dict[str, Any]],
    profile_ids_by_scenario: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the published failure list without treating resume skips as failures."""
    return [
        {
            "scenario_id": item.get("scenario_id"),
            "profile_id": profile_ids_by_scenario.get(
                str(item.get("scenario_id"))
            ),
            "domain": item.get("domain"),
            "failure_type": item.get("failure_type"),
            "failure_reason": item.get("failure_reason"),
            "attempts": item.get("attempts"),
            "trajectory_status": item.get("trajectory_status"),
            "failed_required_servers": item.get("failed_required_servers", []),
            "optional_server_failures": item.get("optional_server_failures", []),
            "output": item.get("output"),
            "log_prefix": item.get("log_prefix"),
        }
        for item in results
        if (
            item.get("status") not in {"completed", "skipped"}
            or not item.get("usable")
        )
    ]


def terminate_process_group(
    process: subprocess.Popen[Any], grace_period: float
) -> None:
    """Terminate a scenario and every MCP descendant in its process group."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            if process.poll() is not None:
                return
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_period)
    except subprocess.TimeoutExpired:
        cleanup_process_group(process)
        process.wait()
    else:
        cleanup_process_group(process)


def cleanup_process_group(process: subprocess.Popen[Any]) -> None:
    """Kill MCP descendants left behind after the scenario process exits.

    Some stdio MCP launchers use a short-lived wrapper process (for example
    ``uv run``).  The wrapper can exit without reaping the Python server it
    launched, so waiting for the scenario leader is not enough.  Every
    scenario runs in its own session; killing that now-unused process group is
    therefore both scoped and deterministic.
    """
    if os.name != "posix":
        if process.poll() is None:
            process.kill()
        return
    # A launcher wrapper can finish while its final child is still being
    # handed off to init.  Retry briefly so that this hand-off cannot race the
    # one-shot group kill and accumulate hundreds of orphaned MCP servers.
    for attempt in range(5):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if attempt < 4:
            time.sleep(0.05)


def run_isolated_subprocess(
    command: list[str],
    *,
    wall_timeout: float,
    termination_grace_period: float,
    output_handle: Any = None,
    active_processes: dict[int, subprocess.Popen[Any]] | None = None,
    active_processes_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    """Run one case in a fresh process group and enforce a wall-clock limit."""
    started = time.monotonic()
    kwargs: dict[str, Any] = {
        "cwd": PROJECT_DIR,
        "start_new_session": os.name == "posix",
    }
    if output_handle is not None:
        kwargs.update(
            {
                "stdout": output_handle,
                "stderr": subprocess.STDOUT,
            }
        )
    process = subprocess.Popen(command, **kwargs)
    if active_processes is not None and active_processes_lock is not None:
        with active_processes_lock:
            active_processes[process.pid] = process
    timed_out = False
    try:
        try:
            returncode = process.wait(timeout=wall_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process_group(process, termination_grace_period)
            returncode = process.returncode
    finally:
        cleanup_process_group(process)
        if active_processes is not None and active_processes_lock is not None:
            with active_processes_lock:
                active_processes.pop(process.pid, None)
    return {
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def terminate_active_processes(
    active_processes: dict[int, subprocess.Popen[Any]],
    active_processes_lock: threading.Lock,
    grace_period: float,
) -> None:
    """Stop every in-flight scenario process group after a batch interruption."""
    with active_processes_lock:
        processes = list(active_processes.values())
    if not processes:
        return

    for process in processes:
        if process.poll() is not None:
            continue
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            continue

    deadline = time.monotonic() + grace_period
    while time.monotonic() < deadline:
        if all(process.poll() is not None for process in processes):
            break
        time.sleep(0.1)

    for process in processes:
        cleanup_process_group(process)


def build_single_command(
    args: argparse.Namespace,
    scenario_id: str,
    output_path: Path,
) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "run_agent_scenario.py"),
        "--scenario-id", scenario_id,
        "--profiles", str(args.profiles.resolve()),
        "--scenarios", str(args.scenarios.resolve()),
        "--user-policy", str(args.user_policy.resolve()),
        "--domains-config", str(args.domains_config.resolve()),
        "--env-config", str(args.env_config.resolve()),
        "--output", str(output_path),
    ]
    for field in (
        "max_user_turns", "timeout", "sim_max_tokens", "sim_temperature",
        "max_agent_rounds",
        "max_tool_calls",
        "max_total_tokens", "sim_format_attempts", "sim_model", "sim_base_url",
        "agent_model",
        "agent_base_url", "agent_api_url",
    ):
        value = getattr(args, field)
        if value is not None:
            command.extend(["--" + field.replace("_", "-"), str(value)])
    shared_mcp_config = getattr(args, "shared_mcp_config", None)
    if shared_mcp_config is not None:
        command.extend(["--shared-mcp-config", str(Path(shared_mcp_config).resolve())])
    return command


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a batch of MCP agent env cases.")
    parser.add_argument(
        "--scenarios", type=Path,
        default=DEFAULT_SCENARIOS,
    )
    parser.add_argument(
        "--profiles", type=Path,
        default=DEFAULT_PROFILES,
    )
    parser.add_argument(
        "--user-policy", type=Path,
        default=PROJECT_DIR / "config/user_simulator_policy.txt",
    )
    parser.add_argument(
        "--domains-config", type=Path,
        default=PROJECT_DIR / "config/domains.json",
    )
    parser.add_argument(
        "--env-config", type=Path,
        default=PROJECT_DIR / "config/agent_env.json",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_DIR / "artifacts/trajectory_runs/latest",
    )
    parser.add_argument(
        "--successful-output-dir",
        type=Path,
        help="Optional publish directory containing only usable trajectories.",
    )
    parser.add_argument(
        "--failure-list",
        type=Path,
        help="Optional JSONL path for terminally failed scenarios.",
    )
    parser.add_argument("--scenario-ids", nargs="+")
    parser.add_argument("--domains", nargs="+")
    parser.add_argument(
        "--one-per-profile",
        action="store_true",
        help="Select exactly one balanced-domain scenario for each profile.",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of isolated scenario subprocesses to run concurrently.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--max-recovery-rounds", type=int, default=3)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Retries per scenario after a failed or unusable trajectory.",
    )
    parser.add_argument("--retry-delay", type=float, default=5.0)
    parser.add_argument(
        "--wall-timeout",
        type=float,
        default=1800.0,
        help="Maximum wall-clock seconds for one scenario subprocess.",
    )
    parser.add_argument(
        "--termination-grace-period",
        type=float,
        default=10.0,
        help="Seconds between terminating and killing a timed-out process group.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="Per-attempt subprocess logs; defaults to <output-dir>/_logs.",
    )
    parser.add_argument(
        "--stream-subprocess-output",
        action="store_true",
        help="Stream child output instead of writing one log per attempt.",
    )
    parser.add_argument("--max-user-turns", type=int)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--sim-max-tokens", type=int)
    parser.add_argument("--sim-temperature", type=float)
    parser.add_argument("--sim-format-attempts", type=int)
    parser.add_argument("--max-agent-rounds", type=int)
    parser.add_argument("--max-tool-calls", type=int)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--sim-model")
    parser.add_argument("--sim-base-url")
    parser.add_argument("--agent-model")
    parser.add_argument("--agent-base-url")
    parser.add_argument("--agent-api-url")
    parser.add_argument(
        "--no-shared-mcp",
        action="store_true",
        help="Start MCP servers inside every trajectory process instead of sharing them.",
    )
    parser.add_argument(
        "--shared-mcp-startup-timeout",
        type=float,
        default=180.0,
        help="Seconds to wait for the batch-scoped shared MCP pool.",
    )
    return parser


def run_batch(args: argparse.Namespace) -> int:
    if (
        args.workers <= 0
        or args.retries < 0
        or args.retry_delay < 0
        or args.wall_timeout <= 0
        or args.termination_grace_period < 0
        or args.max_recovery_rounds < 0
        or args.shared_mcp_startup_timeout <= 0
    ):
        print(
            "Error: workers and wall-timeout must be positive; "
            "retries, retry-delay, and termination-grace-period cannot be negative",
            file=sys.stderr,
        )
        return 1
    try:
        selected = select_cases(read_scenarios(args.scenarios.resolve()), args)
        from agent_env.domain_registry import load_domain_registry

        registry = load_domain_registry(args.domains_config.resolve())
        unknown_domains = sorted(
            {
                str(item.get("domain"))
                for item in selected
                if item.get("domain") not in registry
            }
        )
        if unknown_domains:
            raise ValueError(
                f"Scenarios use unsupported domains: {unknown_domains}"
            )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    required_servers_by_domain = {
        domain: list(spec.core_servers) for domain, spec in registry.items()
    }

    if args.dry_run:
        for index, item in enumerate(selected, start=1):
            print(
                f"[{index}/{len(selected)}] {item['scenario_id']} "
                f"profile={item.get('profile_id')} domain={item.get('domain')}"
            )
        return 0

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or output_dir.name
    model = os.environ.get("MY_MODEL_NAME", "")
    simulator_model = args.sim_model or os.environ.get("SIMULATOR_MODEL_NAME") or model
    agent_model = args.agent_model or os.environ.get("AGENT_MODEL_NAME") or model
    try:
        require_model(
            simulator_model,
            stage="trajectory simulator",
            expected_model=TRAJECTORY_MODEL,
        )
        require_model(
            agent_model,
            stage="trajectory agent",
            expected_model=TRAJECTORY_MODEL,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    from llm.direct_pool import direct_pool_audit

    direct_api = direct_pool_audit()
    if direct_api is None:
        print(
            "Error: trajectory generation requires the direct multi-key API pool",
            file=sys.stderr,
        )
        return 1
    recovery_root = output_dir / "_recovery"
    try:
        recovery_manifest = ensure_recovery_manifest(
            recovery_root / "manifest.json",
            RecoveryContract(
                run_id=run_id,
                phase="trajectory",
                model=agent_model,
                prompt_version="agent_env_trajectory",
                seed=None,
                input_hashes=file_hashes(
                    [
                        args.profiles.resolve(),
                        args.scenarios.resolve(),
                        args.user_policy.resolve(),
                        args.domains_config.resolve(),
                        args.env_config.resolve(),
                    ]
                ),
                config={
                    "simulator_model": simulator_model,
                    "agent_model": agent_model,
                    "wall_timeout": args.wall_timeout,
                    "max_user_turns": args.max_user_turns,
                    "max_agent_rounds": args.max_agent_rounds,
                    "max_tool_calls": args.max_tool_calls,
                    "max_total_tokens": args.max_total_tokens,
                    "direct_api": direct_api,
                    "shared_mcp": not args.no_shared_mcp,
                },
                required_model=TRAJECTORY_MODEL,
            ),
            workers=args.workers,
            resume=args.resume,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    ledger = FailureLedger(recovery_root, phase="trajectory", run_id=run_id)
    log_dir = (args.log_dir or output_dir / "_logs").resolve()
    if not args.stream_subprocess_output:
        log_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    results: list[dict[str, Any]] = []
    runnable: list[tuple[int, dict[str, Any], Path, int]] = []
    stop_event = threading.Event()
    active_processes: dict[int, subprocess.Popen[Any]] = {}
    active_processes_lock = threading.Lock()
    for index, item in enumerate(selected, start=1):
        scenario_id = item["scenario_id"]
        output_path = output_dir / f"{scenario_id}.json"
        required_servers = required_servers_by_domain[item["domain"]]
        existing_assessment = assess_trajectory(output_path, required_servers)
        if args.resume and existing_assessment["usable"]:
            print(f"[{index}/{len(selected)}] skip {scenario_id} (already exists)")
            results.append(
                {
                    "scenario_id": scenario_id,
                    "domain": item.get("domain"),
                    "status": "skipped",
                    "usable": True,
                    "failure_type": None,
                    "failure_reason": None,
                    "optional_server_failures": existing_assessment.get(
                        "failed_optional_servers", []
                    ),
                    "output": str(output_path),
                }
            )
            ledger.resolve(scenario_id, metadata={"output": str(output_path)})
            continue
        recovery_round = ledger.recovery_round(scenario_id, resume=args.resume)
        if recovery_round > args.max_recovery_rounds:
            exhausted_result = {
                    "scenario_id": scenario_id,
                    "domain": item.get("domain"),
                    "status": "failed",
                    "usable": False,
                    "attempts": 0,
                    "recovery_round": recovery_round,
                    "failure_type": "recovery_exhausted",
                    "failure_reason": "maximum recovery rounds exhausted",
                    "output": str(output_path),
                }
            results.append(exhausted_result)
            ledger.record_failure(
                scenario_id,
                error_type="recovery_exhausted",
                error="maximum recovery rounds exhausted",
                attempts=0,
                recovery_round=recovery_round,
                metadata={"domain": item.get("domain")},
            )
            continue
        if args.resume and output_path.exists():
            quarantine_artifact(
                output_path,
                recovery_root / "quarantine",
                item_id=scenario_id,
                reason=str(existing_assessment.get("failure_reason") or "unusable"),
            )
        runnable.append((index, item, output_path, recovery_round))

    shared_pool = None
    args.shared_mcp_config = None
    if runnable and not args.no_shared_mcp:
        from agent_env.shared_mcp import SharedMCPPool

        shared_servers = list(
            dict.fromkeys(
                server_name
                for _, item, _, _ in runnable
                for server_name in required_servers_by_domain[item["domain"]]
            )
        )
        shared_pool = SharedMCPPool(
            project_dir=PROJECT_DIR,
            commands_path=PROJECT_DIR / "mcp_servers/commands.json",
            server_names=shared_servers,
            runtime_dir=output_dir / "_shared_mcp",
            startup_timeout=args.shared_mcp_startup_timeout,
        )
        try:
            args.shared_mcp_config = shared_pool.start()
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Error: unable to start shared MCP pool: {exc}", file=sys.stderr)
            return 1
        print(
            f"Shared MCP pool ready: servers={len(shared_servers)} "
            f"config={args.shared_mcp_config}",
            flush=True,
        )

    selected_order = {
        item["scenario_id"]: index for index, item in enumerate(selected)
    }

    def current_summary() -> dict[str, Any]:
        ordered = sorted(results, key=lambda item: selected_order[item["scenario_id"]])
        return {
            "schema_version": "2.0",
            "started_at": started_at,
            "updated_at": time.time(),
            "completed_at": None,
            "selected_count": len(selected),
            "runnable_count": len(runnable),
            "finished_count": len(results),
            "completed_count": sum(item["status"] == "completed" for item in results),
            "failed_count": sum(item["status"] == "failed" for item in results),
            "skipped_count": sum(item["status"] == "skipped" for item in results),
            "infrastructure_failure_count": sum(
                item.get("failure_type") == "infrastructure" for item in results
            ),
            "fatal_provider_error_count": sum(
                item.get("returncode") == 42 for item in results
            ),
            "trajectory_quality_failure_count": sum(
                item.get("failure_type") == "trajectory_quality" for item in results
            ),
            "timed_out_count": sum(item.get("timed_out") is True for item in results),
            "optional_server_degraded_count": sum(
                bool(item.get("optional_server_failures")) for item in results
            ),
            "shared_mcp": {
                "enabled": shared_pool is not None,
                "servers": (
                    list(shared_pool.server_names) if shared_pool is not None else []
                ),
            },
            "results": ordered,
        }

    summary_path = output_dir / "_batch_summary.json"
    write_json(summary_path, current_summary())

    def run_case(
        index: int,
        item: dict[str, Any],
        output_path: Path,
        recovery_round: int,
    ) -> dict[str, Any]:
        scenario_id = item["scenario_id"]
        print(
            f"[{index}/{len(selected)}] run {scenario_id} "
            f"domain={item.get('domain')}",
            flush=True,
        )
        returncode = 1
        usable = False
        attempts = 0
        attempt_details: list[dict[str, Any]] = []
        final_assessment: dict[str, Any] = {
            "usable": False,
            "failure_type": "infrastructure",
            "failure_reason": "not_started",
        }
        required_servers = required_servers_by_domain[item["domain"]]
        for attempt in range(1, args.retries + 2):
            if stop_event.is_set():
                final_assessment = {
                    "usable": False,
                    "failure_type": "infrastructure",
                    "failure_reason": "batch_interrupted",
                }
                break
            attempts = attempt
            log_path = log_dir / f"{scenario_id}.attempt{attempt}.log"
            command = build_single_command(args, scenario_id, output_path)
            try:
                if args.stream_subprocess_output:
                    execution = run_isolated_subprocess(
                        command,
                        wall_timeout=args.wall_timeout,
                        termination_grace_period=args.termination_grace_period,
                        active_processes=active_processes,
                        active_processes_lock=active_processes_lock,
                    )
                else:
                    with log_path.open("w", encoding="utf-8") as log_handle:
                        execution = run_isolated_subprocess(
                            command,
                            wall_timeout=args.wall_timeout,
                            termination_grace_period=args.termination_grace_period,
                            output_handle=log_handle,
                            active_processes=active_processes,
                            active_processes_lock=active_processes_lock,
                        )
                returncode = execution["returncode"]
                assessment = assess_trajectory(output_path, required_servers)
                if execution["timed_out"]:
                    final_assessment = {
                        **assessment,
                        "usable": False,
                        "failure_type": "infrastructure",
                        "failure_reason": "wall_clock_timeout",
                    }
                elif returncode != 0 and assessment["usable"]:
                    final_assessment = {
                        **assessment,
                        "usable": False,
                        "failure_type": "infrastructure",
                        "failure_reason": "child_nonzero_exit",
                    }
                else:
                    final_assessment = assessment
                if returncode == 42:
                    stop_event.set()
                    final_assessment = {
                        **final_assessment,
                        "usable": False,
                        "failure_type": "infrastructure",
                        "failure_reason": "fatal_provider_error",
                    }
                attempt_details.append(
                    {
                        "attempt": attempt,
                        **execution,
                        "usable": final_assessment["usable"],
                        "failure_type": final_assessment.get("failure_type"),
                        "failure_reason": final_assessment.get("failure_reason"),
                        "trajectory_status": final_assessment.get(
                            "trajectory_status"
                        ),
                        "log": (
                            None
                            if args.stream_subprocess_output
                            else str(log_path)
                        ),
                    }
                )
            except OSError as exc:
                returncode = None
                final_assessment = {
                    "usable": False,
                    "failure_type": "infrastructure",
                    "failure_reason": "child_launch_error",
                }
                attempt_details.append(
                    {
                        "attempt": attempt,
                        "returncode": None,
                        "timed_out": False,
                        "usable": False,
                        "failure_type": "infrastructure",
                        "failure_reason": "child_launch_error",
                        "error": str(exc),
                        "log": (
                            None
                            if args.stream_subprocess_output
                            else str(log_path)
                        ),
                    }
                )
            usable = bool(final_assessment["usable"])
            if returncode == 42:
                break
            if returncode == 0 and usable:
                break
            if output_path.exists():
                quarantine_artifact(
                    output_path,
                    recovery_root / "quarantine",
                    item_id=scenario_id,
                    reason=str(
                        final_assessment.get("failure_reason")
                        or "trajectory_attempt_failed"
                    ),
                )
            if stop_event.is_set():
                final_assessment = {
                    **final_assessment,
                    "usable": False,
                    "failure_type": "infrastructure",
                    "failure_reason": "batch_interrupted",
                }
                break
            if attempt <= args.retries:
                print(
                    f"[{index}/{len(selected)}] retry {scenario_id} "
                    f"attempt={attempt + 1}/{args.retries + 1} "
                    f"returncode={returncode} usable={usable}",
                    flush=True,
                )
                time.sleep(args.retry_delay * attempt)

        status = "completed" if returncode == 0 and usable else "failed"
        print(
            f"[{index}/{len(selected)}] {status} {scenario_id} attempts={attempts}",
            flush=True,
        )
        return {
            "scenario_id": scenario_id,
            "domain": item.get("domain"),
            "status": status,
            "returncode": returncode,
            "usable": usable,
            "attempts": attempts,
            "recovery_round": recovery_round,
            "attempt_details": attempt_details,
            "timed_out": any(
                detail.get("timed_out") is True for detail in attempt_details
            ),
            "failure_type": (
                None if status == "completed" else final_assessment["failure_type"]
            ),
            "failure_reason": (
                None if status == "completed" else final_assessment["failure_reason"]
            ),
            "trajectory_status": final_assessment.get("trajectory_status"),
            "evaluator_passed": final_assessment.get("evaluator_passed"),
            "failed_required_servers": final_assessment.get(
                "failed_required_servers", []
            ),
            "optional_server_failures": final_assessment.get(
                "failed_optional_servers", []
            ),
            "output": str(output_path),
            "log_prefix": None if args.stream_subprocess_output else str(log_dir / scenario_id),
        }

    executor = ThreadPoolExecutor(max_workers=args.workers)
    future_to_case: dict[Any, tuple[int, dict[str, Any]]] = {}
    try:
        try:
            future_to_case = {
                executor.submit(
                    run_case, index, item, output_path, recovery_round
                ): (index, item)
                for index, item, output_path, recovery_round in runnable
            }
            for future in as_completed(future_to_case):
                result = future.result()
                results.append(result)
                scenario_id = str(result["scenario_id"])
                if result["status"] == "completed" and result.get("usable"):
                    ledger.resolve(
                        scenario_id, metadata={"output": result.get("output")}
                    )
                else:
                    ledger.record_failure(
                        scenario_id,
                        error_type=str(result.get("failure_type") or "trajectory"),
                        error=str(result.get("failure_reason") or "trajectory failed"),
                        attempts=int(result.get("attempts", 0)),
                        recovery_round=int(result.get("recovery_round", 0)),
                        metadata={
                            "domain": result.get("domain"),
                            "output": result.get("output"),
                        },
                    )
                write_json(summary_path, current_summary())
                if args.fail_fast and results[-1]["status"] == "failed":
                    for pending in future_to_case:
                        pending.cancel()
                    break
        except KeyboardInterrupt:
            stop_event.set()
            for pending in future_to_case:
                pending.cancel()
            terminate_active_processes(
                active_processes,
                active_processes_lock,
                args.termination_grace_period,
            )
            executor.shutdown(wait=True, cancel_futures=True)
            finished_ids = {str(item["scenario_id"]) for item in results}
            for _, item, _, recovery_round in runnable:
                scenario_id = str(item["scenario_id"])
                if scenario_id in finished_ids:
                    continue
                ledger.record_failure(
                    scenario_id,
                    error_type="interrupted",
                    error="trajectory batch interrupted before terminal accounting",
                    attempts=0,
                    recovery_round=recovery_round,
                    metadata={"domain": item.get("domain")},
                )
            write_json(summary_path, current_summary())
            mark_recovery_manifest_interrupted(
                recovery_root / "manifest.json",
                unresolved_failure_count=len(ledger.unresolved()),
            )
            print(
                f"Batch interrupted: completed={current_summary()['completed_count']} "
                f"failed={current_summary()['failed_count']} -> {output_dir}. "
                "Re-run with --resume to continue.",
                file=sys.stderr,
            )
            return 130
        else:
            executor.shutdown(wait=True, cancel_futures=True)
    finally:
        if shared_pool is not None:
            shared_pool.stop()

    results.sort(key=lambda item: selected_order[item["scenario_id"]])

    summary = current_summary()
    summary["completed_at"] = time.time()
    if args.successful_output_dir:
        successful_dir = args.successful_output_dir.resolve()
        successful_dir.mkdir(parents=True, exist_ok=True)
        for item in summary["results"]:
            if (
                item.get("status") not in {"completed", "skipped"}
                or not item.get("usable")
            ):
                continue
            source = Path(str(item["output"])).resolve()
            if source.is_file():
                atomic_write_text(
                    successful_dir / source.name,
                    source.read_text(encoding="utf-8"),
                )
        summary["successful_output_dir"] = str(successful_dir)
    if args.failure_list:
        failure_path = args.failure_list.resolve()
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        profile_ids_by_scenario = {
            str(record.get("scenario_id")): record.get("profile_id")
            for record in read_scenarios(args.scenarios.resolve())
        }
        failures = build_failure_records(
            summary["results"], profile_ids_by_scenario
        )
        atomic_write_jsonl(failure_path, failures)
        summary["failure_list"] = str(failure_path)
        summary["published_failure_count"] = len(failures)
    write_json(summary_path, summary)
    recovery_manifest.update(
        {
            "status": "completed" if summary["failed_count"] == 0 else "failed",
            "updated_at": time.time(),
            "completed_at": time.time(),
            "unresolved_failure_count": len(ledger.unresolved()),
        }
    )
    atomic_write_json(recovery_root / "manifest.json", recovery_manifest)
    print(
        f"Batch complete: completed={summary['completed_count']} "
        f"failed={summary['failed_count']} skipped={summary['skipped_count']} -> {output_dir}"
    )
    if summary["fatal_provider_error_count"]:
        return 42
    return 1 if summary["failed_count"] else 0


def main() -> int:
    load_project_env(PROJECT_DIR)
    args = build_arg_parser().parse_args()
    try:
        with termination_signal_handlers():
            return run_batch(args)
    except KeyboardInterrupt:
        # Covers interruption before worker lifecycle initialization. The
        # active-run path above performs complete child/MCP cleanup and writes
        # an interrupted manifest before returning 130.
        mark_recovery_manifest_interrupted(
            args.output_dir.resolve() / "_recovery/manifest.json"
        )
        print("Batch interrupted before workers started.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
