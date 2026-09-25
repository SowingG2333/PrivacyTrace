#!/usr/bin/env python3
"""Run schema-only, server-view, and trajectory-channel attacks concurrently."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from agent_env.recovery import (  # noqa: E402
    FailureLedger,
    RecoveryContract,
    RollingFailureWindow,
    RollingRateLimiter,
    atomic_write_json,
    ensure_recovery_manifest,
    file_hashes,
    is_fatal_provider_error,
    is_nonretryable_request_error,
    is_transient_provider_error,
    portable_path,
    quarantine_artifact,
    require_nonempty_model,
)
from privacy_trace.llm_client import DirectOpenAIClient  # noqa: E402
from privacy_trace.trajectory_io import (  # noqa: E402
    TrajectoryRecordRef,
    index_trajectories,
    trajectory_input_files,
)
from privacy_trace.attacker_view import (  # noqa: E402
    ATTACKER_OBSERVATION_FORMAT,
    ATTACKER_OBSERVATION_FORMATS,
    attacker_view_policy_for_format,
)


@dataclass(frozen=True)
class ExperimentJob:
    scenario_id: str
    profile_id: str
    domain: str
    mode: str
    trajectory_path: TrajectoryRecordRef
    output_path: Path
    server_name: str | None = None

    @property
    def job_id(self) -> str:
        return (
            f"{self.mode}:{self.scenario_id}:{self.server_name}"
            if self.server_name is not None
            else f"{self.mode}:{self.scenario_id}"
        )


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def is_usable_output(
    path: Path,
    mode: str,
    server_name: str | None,
    *,
    model: str | None = None,
    trajectory_sha256: str | None = None,
    observation_format: str = ATTACKER_OBSERVATION_FORMAT,
) -> bool:
    from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES

    try:
        value = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    metadata = value.get("metadata")
    profile = value.get("profile")
    if not isinstance(metadata, dict) or metadata.get("experiment_mode") != mode:
        return False
    if (
        metadata.get("attacker_view_policy")
        != attacker_view_policy_for_format(observation_format)
    ):
        return False
    if metadata.get("attacker_observation_format") != observation_format:
        return False
    if mode == "server_view" and metadata.get("server_name") != server_name:
        return False
    if model is not None and metadata.get("model") != model:
        return False
    if (
        trajectory_sha256 is not None
        and metadata.get("trajectory_sha256") != trajectory_sha256
    ):
        return False
    if not isinstance(profile, dict) or set(profile) != set(PROFILE_ATTRIBUTES):
        return False
    if metadata.get("failure_scored_as_incorrect") is True:
        return all(
            isinstance(profile.get(attribute), dict)
            and profile[attribute].get("status") == "failed"
            and profile[attribute].get("value") is None
            for attribute in PROFILE_ATTRIBUTES
        )
    return all(
        isinstance(profile.get(attribute), dict)
        and profile[attribute].get("status") == "inferred"
        and profile[attribute].get("value") is not None
        for attribute in PROFILE_ATTRIBUTES
    )


def build_jobs(
    input_path: Path,
    output_root: Path,
    modes: set[str],
    limit: int | None,
    observation_format: str = ATTACKER_OBSERVATION_FORMAT,
) -> tuple[list[ExperimentJob], dict[str, str]]:
    from privacy_trace.channel_experiments import SERVER_VIEW, server_slug, unique_server_names

    paths = index_trajectories(input_path, limit)

    jobs: list[ExperimentJob] = []
    server_slugs: dict[str, str] = {}
    for path in paths:
        trajectory = path.read()
        scenario_id = str(trajectory.get("scenario_id") or path.stem)
        profile_id = str(trajectory.get("profile_id") or "")
        domain = str(trajectory.get("domain") or "")
        tools_info = trajectory.get("tools_info")
        if not isinstance(tools_info, list):
            raise ValueError(f"Trajectory has no tools_info array: {path}")

        for mode in sorted(modes - {SERVER_VIEW}):
            jobs.append(
                ExperimentJob(
                    scenario_id=scenario_id,
                    profile_id=profile_id,
                    domain=domain,
                    mode=mode,
                    trajectory_path=path,
                    output_path=output_root / mode / f"{scenario_id}.json",
                )
            )
        if SERVER_VIEW in modes:
            for server_name in unique_server_names(
                tools_info,
                observation_format,
            ):
                slug = server_slugs.setdefault(server_name, server_slug(server_name))
                jobs.append(
                    ExperimentJob(
                        scenario_id=scenario_id,
                        profile_id=profile_id,
                        domain=domain,
                        mode=SERVER_VIEW,
                        trajectory_path=path,
                        output_path=(
                            output_root / SERVER_VIEW / slug / f"{scenario_id}.json"
                        ),
                        server_name=server_name,
                    )
                )
    return jobs, server_slugs


def build_arg_parser() -> argparse.ArgumentParser:
    from privacy_trace.channel_experiments import GENERATED_MODES

    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--input",
        type=Path,
        help="Canonical trajectories.jsonl or a legacy per-record directory.",
    )
    input_group.add_argument(
        "--input-dir",
        type=Path,
        help="Legacy alias for a per-record trajectory directory.",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=list(GENERATED_MODES) + ["all"],
        default=["all"],
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--rpm", type=int, default=500)
    parser.add_argument(
        "--observation-format",
        choices=ATTACKER_OBSERVATION_FORMATS,
        default=ATTACKER_OBSERVATION_FORMAT,
        help="Tool-call representation: original raw baseline or lossless catalog.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--max-recovery-rounds", type=int, default=5)
    parser.add_argument("--materialize-exhausted", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument(
        "--model",
        help="Model ID override; defaults to MY_MODEL_NAME from the environment.",
    )
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument(
        "--thinking-mode", choices=["disabled", "enabled"], default="disabled"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Per-request timeout; keep above the service's queued generation time.",
    )
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--summary-every", type=int, default=20)
    return parser


def main() -> int:
    from agent_env.settings import load_project_env
    from privacy_trace.channel_experiments import (
        GENERATED_MODES,
        SCHEMA_ONLY,
        build_observation_prompt_details,
        build_schema_only_prompt,
        parse_and_validate_prediction,
    )

    load_project_env(PROJECT_DIR)
    args = build_arg_parser().parse_args()
    if args.max_tokens is not None:
        print(
            "Error: Privacy Attack V1 uses the service-default output limit; "
            "omit --max-tokens.",
            file=sys.stderr,
        )
        return 1
    if (
        args.workers <= 0
        or args.retries < 0
        or args.retry_delay < 0
        or args.max_recovery_rounds < 0
        or args.rpm <= 0
    ):
        print("Error: invalid worker or retry settings", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit <= 0:
        print("Error: --limit must be positive", file=sys.stderr)
        return 2

    modes = set(GENERATED_MODES if "all" in args.modes else args.modes)
    input_path = args.input or args.input_dir
    if input_path is None:
        print("Error: one of --input or --input-dir is required", file=sys.stderr)
        return 2
    input_path = input_path.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        jobs, server_slugs = build_jobs(
            input_path,
            output_root,
            modes,
            args.limit,
            args.observation_format,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    model = args.model or os.environ.get("MY_MODEL_NAME")
    base_url = os.environ.get("MY_MODEL_BASE_URL")
    try:
        key_pool_size = DirectOpenAIClient.environment_key_count()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not model or not base_url or key_pool_size <= 0:
        print("Error: MY_MODEL_NAME, MY_MODEL_BASE_URL, and an enabled model API key are required", file=sys.stderr)
        return 2
    try:
        require_nonempty_model(model, stage="privacy channel experiment")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    run_id = args.run_id or output_root.name
    recovery_root = output_root / "_recovery"
    try:
        recovery_manifest = ensure_recovery_manifest(
            recovery_root / "manifest.json",
            RecoveryContract(
                run_id=run_id,
                phase="privacy_channels",
                model=model,
                prompt_version=f"privacy_channel_{args.observation_format}",
                seed=None,
                input_hashes=file_hashes(
                    trajectory_input_files(input_path),
                    relative_to=PROJECT_DIR,
                ),
                config={
                    "modes": sorted(modes),
                    "limit": args.limit,
                    "temperature": args.temperature,
                    "rpm": args.rpm,
                    "max_tokens": None,
                    "output_limit_source": "service_default",
                    "direct_transport": "trust_env_false",
                    "api_key_pool_size": key_pool_size,
                    "thinking_mode": args.thinking_mode,
                    "attacker_view_policy": attacker_view_policy_for_format(
                        args.observation_format
                    ),
                    "attacker_observation_format": args.observation_format,
                },
                required_model=None,
            ),
            workers=args.workers,
            resume=args.resume,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    ledger = FailureLedger(
        recovery_root, phase="privacy_channels", run_id=run_id
    )

    manifest = {
        "schema_version": "1.0",
        "experiment": "privacy_channel_decomposition",
        "model": model,
        "base_url": base_url,
        "input": portable_path(input_path, relative_to=PROJECT_DIR),
        "output_root": portable_path(output_root, relative_to=PROJECT_DIR),
        "modes": sorted(modes),
        "workers": args.workers,
        "api_key_pool_size": key_pool_size,
        "job_count": len(jobs),
        "jobs_per_mode": dict(Counter(job.mode for job in jobs)),
        "server_slugs": server_slugs,
        "attacker_view_policy": attacker_view_policy_for_format(
            args.observation_format
        ),
        "attacker_observation_format": args.observation_format,
    }
    write_json(output_root / "_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0

    runnable: list[tuple[ExperimentJob, int, str]] = []
    results: list[dict[str, Any]] = []
    for job in jobs:
        trajectory_sha256 = job.trajectory_path.sha256
        if args.resume and is_usable_output(
            job.output_path,
            job.mode,
            job.server_name,
            model=model,
            trajectory_sha256=trajectory_sha256,
            observation_format=args.observation_format,
        ):
            results.append({"job_id": job.job_id, "mode": job.mode, "status": "skipped"})
            ledger.resolve(
                job.job_id,
                metadata={
                    "output": portable_path(
                        job.output_path, relative_to=PROJECT_DIR
                    )
                },
            )
        else:
            recovery_round = ledger.recovery_round(job.job_id, resume=args.resume)
            if recovery_round > args.max_recovery_rounds:
                if args.materialize_exhausted:
                    from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES

                    prediction = {
                        attribute: {
                            "status": "failed",
                            "evidence": [],
                            "value": None,
                            "alternative_values": [],
                            "unresolved_reason": "channel recovery exhausted",
                        }
                        for attribute in PROFILE_ATTRIBUTES
                    }
                    write_json(
                        job.output_path,
                        {
                            "profile": prediction,
                            "one_shot": {"profile": prediction},
                            "metadata": {
                                "attack_type": "one_shot",
                                "experiment": "privacy_channel_decomposition",
                                "experiment_mode": job.mode,
                                "model": model,
                                "scenario_id": job.scenario_id,
                                "profile_id": job.profile_id,
                                "domain": job.domain,
                                "server_name": job.server_name,
                                "trajectory_path": job.trajectory_path.display_path,
                                "trajectory_sha256": trajectory_sha256,
                                "attacker_view_policy": attacker_view_policy_for_format(
                                    args.observation_format
                                ),
                                "attacker_observation_format": args.observation_format,
                                "failure_scored_as_incorrect": True,
                                "recovery_round": recovery_round,
                            },
                        },
                    )
                    ledger.resolve(
                        job.job_id,
                        metadata={
                            "output": portable_path(
                                job.output_path, relative_to=PROJECT_DIR
                            ),
                            "materialized_failure": True,
                        },
                    )
                    results.append(
                        {
                            "job_id": job.job_id,
                            "mode": job.mode,
                            "status": "materialized_failure",
                        }
                    )
                    continue
                results.append(
                    {
                        "job_id": job.job_id,
                        "mode": job.mode,
                        "status": "failed",
                        "error": "maximum recovery rounds exhausted",
                        "recovery_round": recovery_round,
                    }
                )
                continue
            if args.resume and job.output_path.exists():
                quarantine_artifact(
                    job.output_path,
                    recovery_root / "quarantine",
                    item_id=job.job_id.replace(":", "_"),
                    reason="unusable_channel_output",
                )
            runnable.append((job, recovery_round, trajectory_sha256))

    client = DirectOpenAIClient.from_environment(
        timeout=args.timeout,
        max_connections=min(args.workers, 200),
    )
    schema_prompt = build_schema_only_prompt()
    stop_event = threading.Event()
    fatal_error: dict[str, str | None] = {"message": None}
    fatal_lock = threading.Lock()
    rate_limiter = RollingRateLimiter(args.rpm)
    provider_health = RollingFailureWindow()
    started_at = time.time()

    def execute(
        job: ExperimentJob, recovery_round: int, trajectory_sha256: str
    ) -> dict[str, Any]:
        if stop_event.is_set():
            return {"job_id": job.job_id, "mode": job.mode, "status": "aborted"}
        last_error = "unknown error"
        for attempt in range(1, args.retries + 2):
            try:
                if job.mode == SCHEMA_ONLY:
                    prompt = schema_prompt
                    observable_call_count = 0
                    observation_metadata = {
                        "attacker_view_policy": attacker_view_policy_for_format(
                            args.observation_format
                        ),
                        "attacker_observation_format": args.observation_format,
                        "raw_call_count": 0,
                        "visible_call_count": 0,
                        "prior_equivalent": True,
                    }
                else:
                    trajectory = job.trajectory_path.read()
                    tools_info = trajectory.get("tools_info")
                    if not isinstance(tools_info, list):
                        raise ValueError("trajectory has no tools_info array")
                    prompt, projected, observation_metadata = build_observation_prompt_details(
                        tools_info,
                        job.mode,
                        server_name=job.server_name,
                        observation_format=args.observation_format,
                    )
                    observable_call_count = len(projected)
                    if observation_metadata.get("prior_equivalent"):
                        prompt = schema_prompt

                response = client.complete_json(
                    prompt=prompt,
                    model=model,
                    temperature=args.temperature,
                    stage=job.mode,
                    limiter=rate_limiter,
                    thinking_mode=args.thinking_mode,
                )
                if response.metadata.get("finish_reason") == "length":
                    raise RuntimeError("response truncated: finish_reason=length")
                raw_output = response.text
                prediction = parse_and_validate_prediction(raw_output)
                result = {
                    "profile": prediction["profile"],
                    "inferred_attributes": prediction["inferred_attributes"],
                    "unresolved_attributes": prediction["unresolved_attributes"],
                    "one_shot": prediction,
                    "metadata": {
                        "attack_type": "one_shot",
                        "experiment": "privacy_channel_decomposition",
                        "experiment_mode": job.mode,
                        "model": model,
                        "temperature": args.temperature,
                        "scenario_id": job.scenario_id,
                        "profile_id": job.profile_id,
                        "domain": job.domain,
                        "server_name": job.server_name,
                        "trajectory_path": job.trajectory_path.display_path,
                        "trajectory_sha256": trajectory_sha256,
                        "recovery_round": recovery_round,
                        "attempt": attempt,
                        "observable_call_count": observable_call_count,
                        **observation_metadata,
                        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        "request": response.metadata,
                    },
                }
                write_json(job.output_path, result)
                provider_health.record(False)
                return {
                    "job_id": job.job_id,
                    "mode": job.mode,
                    "status": "completed",
                    "attempts": attempt,
                    "recovery_round": recovery_round,
                    "output": portable_path(
                        job.output_path, relative_to=PROJECT_DIR
                    ),
                }
            except Exception as exc:
                last_error = str(exc)
                if is_fatal_provider_error(exc):
                    with fatal_lock:
                        if fatal_error["message"] is None:
                            fatal_error["message"] = last_error
                    stop_event.set()
                    break
                if is_nonretryable_request_error(exc):
                    if args.materialize_exhausted:
                        from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES

                        failed_profile = {
                            attribute: {
                                "status": "failed", "evidence": [], "value": None,
                                "alternative_values": [],
                                "unresolved_reason": last_error,
                            }
                            for attribute in PROFILE_ATTRIBUTES
                        }
                        write_json(
                            job.output_path,
                            {
                                "profile": failed_profile,
                                "one_shot": {"profile": failed_profile},
                                "metadata": {
                                    "attack_type": "one_shot",
                                    "experiment": "privacy_channel_decomposition",
                                    "experiment_mode": job.mode,
                                    "model": model,
                                    "scenario_id": job.scenario_id,
                                    "profile_id": job.profile_id,
                                    "domain": job.domain,
                                    "server_name": job.server_name,
                                    "trajectory_path": job.trajectory_path.display_path,
                                    "trajectory_sha256": trajectory_sha256,
                                    "attacker_view_policy": attacker_view_policy_for_format(args.observation_format),
                                    "attacker_observation_format": args.observation_format,
                                    "failure_scored_as_incorrect": True,
                                    "failure_reason": last_error,
                                    "recovery_round": recovery_round,
                                    "attempt": attempt,
                                },
                            },
                        )
                        return {
                            "job_id": job.job_id,
                            "mode": job.mode,
                            "status": "materialized_failure",
                            "attempts": attempt,
                            "recovery_round": recovery_round,
                            "output": portable_path(
                                job.output_path, relative_to=PROJECT_DIR
                            ),
                        }
                    break
                provider_health.record(is_transient_provider_error(exc))
                if attempt <= args.retries:
                    delay = min(30.0, args.retry_delay * (2 ** (attempt - 1)))
                    time.sleep(delay * random.uniform(0.75, 1.25))
        return {
            "job_id": job.job_id,
            "mode": job.mode,
            "status": "failed",
            "attempts": args.retries + 1,
            "recovery_round": recovery_round,
            "error": last_error,
        }

    def summary_value(completed_at: float | None = None) -> dict[str, Any]:
        statuses = Counter(item["status"] for item in results)
        per_mode: dict[str, dict[str, int]] = {}
        for mode in sorted(modes):
            mode_statuses = Counter(
                item["status"] for item in results if item["mode"] == mode
            )
            per_mode[mode] = dict(mode_statuses)
        failures = [
            {
                "job_id": item.get("job_id"),
                "mode": item.get("mode"),
                "attempts": item.get("attempts"),
                "error": item.get("error"),
            }
            for item in results
            if item.get("status") == "failed"
        ]
        return {
            **manifest,
            "started_at": started_at,
            "updated_at": time.time(),
            "completed_at": completed_at,
            "runnable_count": len(runnable),
            "finished_count": len(results),
            "statuses": dict(statuses),
            "per_mode_statuses": per_mode,
            "failure_count": len(failures),
            "materialized_failure_count": sum(
                item["status"] == "materialized_failure" for item in results
            ),
            "failure_examples": failures[:20],
            "fatal_error": fatal_error["message"],
        }

    print(
        f"Starting {len(runnable)} API calls with workers={args.workers}; "
        f"resume_skips={len(jobs) - len(runnable)}",
        flush=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    execute, job, recovery_round, trajectory_sha256
                ): job
                for job, recovery_round, trajectory_sha256 in runnable
            }
            for finished_index, future in enumerate(as_completed(futures), start=1):
                item = future.result()
                results.append(item)
                job_id = str(item["job_id"])
                if item["status"] in {"completed", "materialized_failure"}:
                    ledger.resolve(job_id, metadata={"output": item.get("output")})
                else:
                    ledger.record_failure(
                        job_id,
                        error_type="privacy_channel",
                        error=str(item.get("error") or item["status"]),
                        attempts=int(item.get("attempts", 0)),
                        recovery_round=int(item.get("recovery_round", 0)),
                        metadata={"mode": item.get("mode")},
                    )
                if (
                    finished_index % args.summary_every == 0
                    or finished_index == len(runnable)
                ):
                    elapsed = max(time.time() - started_at, 1e-9)
                    completed = sum(
                        result["status"] == "completed" for result in results
                    )
                    failed = sum(result["status"] == "failed" for result in results)
                    print(
                        f"progress={finished_index}/{len(runnable)} "
                        f"completed={completed} failed={failed} "
                        f"throughput={finished_index / elapsed:.2f} jobs/s",
                        flush=True,
                    )
                    write_json(output_root / "_batch_summary.json", summary_value())
    finally:
        client.close()

    summary = summary_value(completed_at=time.time())
    write_json(output_root / "_batch_summary.json", summary)
    recovery_manifest.update(
        {
            "status": "completed" if summary["failure_count"] == 0 else "failed",
            "updated_at": time.time(),
            "completed_at": time.time(),
            "unresolved_failure_count": len(ledger.unresolved()),
        }
    )
    write_json(recovery_root / "manifest.json", recovery_manifest)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if summary["fatal_error"]:
        return 42
    return 1 if summary["statuses"].get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
