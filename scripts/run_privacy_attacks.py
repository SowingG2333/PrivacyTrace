#!/usr/bin/env python3
"""Run one-shot or two-stage privacy attacks over trajectories concurrently."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from privacy_trace.llm_client import DirectOpenAIClient  # noqa: E402


def select_trajectory_files(
    input_path: Path, limit: int | None
) -> list[TrajectoryRecordRef]:
    return index_trajectories(input_path, limit)


def write_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def is_usable_attack_value(
    value: Any,
    attack_mode: str,
    observation_format: str = ATTACKER_OBSERVATION_FORMAT,
) -> bool:
    """Return whether an attack result is complete enough to resume safely."""
    from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES

    if not isinstance(value, dict):
        return False
    metadata = value.get("metadata")
    profile = value.get("profile")
    if not isinstance(metadata, dict) or metadata.get("attack_type") != attack_mode:
        return False
    if (
        metadata.get("attacker_view_policy")
        != attacker_view_policy_for_format(observation_format)
    ):
        return False
    if metadata.get("attacker_observation_format") != observation_format:
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
    if attack_mode == "two_stage" and (
        metadata.get("protocol_version") != "two_stage_conditional_prior"
        or metadata.get("stage2_context") != "stage1_values_only"
        or metadata.get("stage2_has_trajectory") is not False
    ):
        return False
    return all(
        isinstance(profile.get(attribute), dict)
        and profile[attribute].get("status") == "inferred"
        and "value" in profile[attribute]
        for attribute in PROFILE_ATTRIBUTES
    )


def has_usable_attack(
    path: Path,
    attack_mode: str,
    *,
    model: str | None = None,
    trajectory_sha256: str | None = None,
    observation_format: str = ATTACKER_OBSERVATION_FORMAT,
) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not is_usable_attack_value(value, attack_mode, observation_format):
        return False
    metadata = value.get("metadata", {})
    if model is not None and metadata.get("model") != model:
        return False
    return not (
        trajectory_sha256 is not None
        and metadata.get("trajectory_sha256") != trajectory_sha256
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run two-stage profile inference attacks concurrently."
    )
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
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--reuse-stage1-dir",
        type=Path,
        help=(
            "Reuse Stage 1 outputs from a previous two-stage run and call only "
            "the current Stage 2 protocol. Valid only with --attack-mode two_stage."
        ),
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--rpm",
        type=int,
        default=500,
        help="Client-side request cap; use 0 for unlimited.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--max-recovery-rounds", type=int, default=5)
    parser.add_argument(
        "--materialize-exhausted",
        action="store_true",
        help="Write a 17-attribute failed prediction after recovery is exhausted.",
    )
    parser.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--model")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--trust-env",
        action="store_true",
        help="Use HTTP(S)_PROXY and related transport settings from the environment.",
    )
    parser.add_argument(
        "--thinking-mode", choices=["disabled", "enabled"], default="disabled"
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=5,
        help="Retries after an API, JSON, or schema failure.",
    )
    parser.add_argument("--retry-delay", type=float, default=5.0)
    parser.add_argument(
        "--summary-every",
        type=int,
        default=10,
        help="Persist a resumable batch summary every N finished attacks.",
    )
    parser.add_argument(
        "--attack-mode",
        choices=["two_stage", "one_shot"],
        default="two_stage",
    )
    parser.add_argument(
        "--observation-format",
        choices=ATTACKER_OBSERVATION_FORMATS,
        default=ATTACKER_OBSERVATION_FORMAT,
        help="Tool-call representation: original raw baseline or lossless catalog.",
    )
    return parser


def main() -> int:
    from agent_env.settings import load_project_env

    load_project_env(PROJECT_DIR)
    args = build_arg_parser().parse_args()
    if (
        args.workers <= 0
        or args.retries < 0
        or args.retry_delay < 0
        or args.max_recovery_rounds < 0
        or args.rpm < 0
    ):
        print(
            "Error: workers must be positive; RPM and retry values must be non-negative",
            file=sys.stderr,
        )
        return 1
    if args.summary_every <= 0:
        print("Error: summary-every must be positive", file=sys.stderr)
        return 1
    if args.reuse_stage1_dir is not None and args.attack_mode != "two_stage":
        print(
            "Error: --reuse-stage1-dir requires --attack-mode two_stage",
            file=sys.stderr,
        )
        return 1

    input_path = args.input or args.input_dir
    if input_path is None:
        print("Error: one of --input or --input-dir is required", file=sys.stderr)
        return 1
    input_path = input_path.resolve()
    try:
        files = select_trajectory_files(input_path, args.limit)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    model = args.model
    if model is None:
        model = os.environ.get("MY_MODEL_NAME") or "glm-5.2"
    try:
        require_nonempty_model(model, stage="privacy attack")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.provider != "openai":
        print("Error: privacy batch runner requires --provider openai", file=sys.stderr)
        return 1
    try:
        key_pool_size = DirectOpenAIClient.environment_key_count()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if key_pool_size <= 0:
        print("Error: no enabled MY_MODEL_API_KEY or MY_MODEL_API_KEYS slot", file=sys.stderr)
        return 1

    from privacy_trace.privacy_attacker import (
        run_one_shot_attack,
        run_stage1_attack,
        run_stage2_from_stage1,
        validate_output,
    )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or output_dir.name
    recovery_root = output_dir / "_recovery"
    reuse_stage1_dir = (
        args.reuse_stage1_dir.resolve()
        if args.reuse_stage1_dir is not None
        else None
    )
    if reuse_stage1_dir is not None and not reuse_stage1_dir.is_dir():
        print(
            f"Error: Stage 1 source directory not found: {reuse_stage1_dir}",
            file=sys.stderr,
        )
        return 1
    input_hashes = file_hashes(
        trajectory_input_files(input_path), relative_to=PROJECT_DIR
    )
    if reuse_stage1_dir is not None:
        input_hashes.update(
            file_hashes(
                sorted(reuse_stage1_dir.glob("S*.json")),
                relative_to=PROJECT_DIR,
            )
        )
    try:
        recovery_manifest = ensure_recovery_manifest(
            recovery_root / "manifest.json",
            RecoveryContract(
                run_id=run_id,
                phase=f"privacy_attack_{args.attack_mode}",
                model=model,
                prompt_version=(
                    f"one_shot_{args.observation_format}"
                    if args.attack_mode == "one_shot"
                    else f"two_stage_{args.observation_format}"
                ),
                seed=None,
                input_hashes=input_hashes,
                config={
                    "attack_mode": args.attack_mode,
                    "provider": args.provider,
                    "temperature": args.temperature,
                    "thinking_mode": args.thinking_mode,
                    "timeout": args.timeout,
                    "max_tokens": None,
                    "output_limit_source": "service_default",
                    "direct_transport": (
                        "trust_env_true" if args.trust_env else "trust_env_false"
                    ),
                    "api_key_pool_size": key_pool_size,
                    "rpm": args.rpm,
                    "limit": args.limit,
                    "reuse_stage1_dir": (
                        str(reuse_stage1_dir) if reuse_stage1_dir else None
                    ),
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
        return 1
    ledger = FailureLedger(
        recovery_root,
        phase=f"privacy_attack_{args.attack_mode}",
        run_id=run_id,
    )
    started_at = time.time()
    results: list[dict[str, Any]] = []
    runnable: list[tuple[int, TrajectoryRecordRef, Path, int, str]] = []
    stop_event = threading.Event()
    fatal_error: dict[str, str | None] = {"message": None}
    fatal_error_lock = threading.Lock()
    rate_limiter = RollingRateLimiter(args.rpm)
    provider_health = RollingFailureWindow()
    client = DirectOpenAIClient.from_environment(
        timeout=args.timeout,
        max_connections=min(args.workers, 200),
        trust_env=args.trust_env,
    )
    stage1_dir = output_dir / "_stage1"

    def write_failed_stage1_checkpoint(
        input_path: TrajectoryRecordRef,
        trajectory_sha256: str,
        trajectory: dict[str, Any],
        *,
        reason: str,
        recovery_round: int,
        attempt: int,
    ) -> None:
        """Preserve a fixed-denominator Stage 1 audit record without sampling.

        A deterministic error can occur before Stage 1 produces a model output
        (for example, a context-length rejection).  The final failed result is
        still valid for the primary metric, but writing this explicit checkpoint
        keeps the two-stage checkpoint audit one-for-one with trajectories.
        """
        if args.attack_mode != "two_stage" or reuse_stage1_dir is not None:
            return
        checkpoint_path = stage1_dir / input_path.name
        if checkpoint_path.exists():
            return
        from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES

        stage1 = {
            "profile": {
                attribute: {
                    "status": "unresolved",
                    "evidence": [],
                    "value": None,
                    "confidence": None,
                    "alternative_values": [],
                    "unresolved_reason": "Stage 1 was not generated: " + reason,
                }
                for attribute in PROFILE_ATTRIBUTES
            },
            "inferred_attributes": [],
            "unresolved_attributes": list(PROFILE_ATTRIBUTES),
        }
        write_json(
            checkpoint_path,
            {
                "stage1": stage1,
                "metadata": {
                    "model": model,
                    "trajectory_sha256": trajectory_sha256,
                    "scenario_id": trajectory.get("scenario_id") or input_path.stem,
                    "profile_id": trajectory.get("profile_id"),
                    "domain": trajectory.get("domain"),
                    "attacker_view_policy": attacker_view_policy_for_format(
                        args.observation_format
                    ),
                    "attacker_observation_format": args.observation_format,
                    "failure_scored_as_incorrect": True,
                    "stage1_generation_failed": True,
                    "failure_reason": reason,
                    "recovery_round": recovery_round,
                    "attempt": attempt,
                    "request": {
                        "stage": "stage1",
                        "model": model,
                        "max_tokens": None,
                        "output_limit_source": "service_default",
                        "direct_transport": (
                            "trust_env_true" if args.trust_env else "trust_env_false"
                        ),
                        "error": reason,
                    },
                },
            },
        )

    def request_fn(prompt: str, stage: str) -> tuple[str, dict[str, Any]]:
        response = client.complete_json(
            prompt=prompt,
            model=model,
            temperature=args.temperature,
            stage=stage,
            limiter=rate_limiter,
            thinking_mode=args.thinking_mode,
        )
        if response.metadata.get("finish_reason") == "length":
            raise RuntimeError("response truncated: finish_reason=length")
        return response.text, response.metadata

    for index, input_path in enumerate(files, start=1):
        output_path = output_dir / input_path.name
        trajectory_sha256 = input_path.sha256
        if args.resume and has_usable_attack(
            output_path,
            args.attack_mode,
            model=model,
            trajectory_sha256=trajectory_sha256,
            observation_format=args.observation_format,
        ):
            print(f"[{index}/{len(files)}] skip {input_path.stem} (already exists)")
            results.append({"scenario_id": input_path.stem, "status": "skipped"})
            ledger.resolve(
                input_path.stem,
                metadata={
                    "output": portable_path(
                        output_path, relative_to=PROJECT_DIR
                    )
                },
            )
        else:
            recovery_round = ledger.recovery_round(
                input_path.stem, resume=args.resume
            )
            if recovery_round > args.max_recovery_rounds:
                if args.materialize_exhausted:
                    from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES
                    trajectory = input_path.read()

                    write_failed_stage1_checkpoint(
                        input_path,
                        trajectory_sha256,
                        trajectory,
                        reason="attack recovery exhausted",
                        recovery_round=recovery_round,
                        attempt=0,
                    )

                    materialized = {
                        "profile": {
                            attribute: {
                                "status": "failed",
                                "evidence": [],
                                "value": None,
                                "alternative_values": [],
                                "unresolved_reason": "attack recovery exhausted",
                            }
                            for attribute in PROFILE_ATTRIBUTES
                        },
                        "metadata": {
                            "attack_type": args.attack_mode,
                            "model": model,
                            "scenario_id": input_path.stem,
                            "profile_id": trajectory.get("profile_id"),
                            "domain": trajectory.get("domain"),
                            "trajectory_path": input_path.display_path,
                            "trajectory_sha256": trajectory_sha256,
                            "attacker_view_policy": attacker_view_policy_for_format(
                                args.observation_format
                            ),
                            "attacker_observation_format": args.observation_format,
                            "failure_scored_as_incorrect": True,
                            "recovery_round": recovery_round,
                        },
                    }
                    write_json(output_path, materialized)
                    ledger.resolve(
                        input_path.stem,
                        metadata={
                            "output": portable_path(
                                output_path, relative_to=PROJECT_DIR
                            ),
                            "materialized_failure": True,
                        },
                    )
                    results.append(
                        {
                            "scenario_id": input_path.stem,
                            "status": "materialized_failure",
                            "attempts": 0,
                            "recovery_round": recovery_round,
                            "output": portable_path(
                                output_path, relative_to=PROJECT_DIR
                            ),
                        }
                    )
                    continue
                results.append(
                    {
                        "scenario_id": input_path.stem,
                        "status": "failed",
                        "attempts": 0,
                        "recovery_round": recovery_round,
                        "error": "maximum recovery rounds exhausted",
                    }
                )
                continue
            if args.resume and output_path.exists():
                quarantine_artifact(
                    output_path,
                    recovery_root / "quarantine",
                    item_id=input_path.stem,
                    reason="unusable_attack_output",
                )
            runnable.append(
                (
                    index,
                    input_path,
                    output_path,
                    recovery_round,
                    trajectory_sha256,
                )
            )

    def attack_one(
        index: int,
        input_path: TrajectoryRecordRef,
        output_path: Path,
        recovery_round: int,
        trajectory_sha256: str,
    ) -> dict[str, Any]:
        scenario_id = input_path.stem
        if stop_event.is_set():
            return {"scenario_id": scenario_id, "status": "aborted", "attempts": 0}
        print(f"[{index}/{len(files)}] attack {scenario_id}", flush=True)
        last_error = "unknown error"
        for attempt in range(1, args.retries + 2):
            if stop_event.is_set():
                return {
                    "scenario_id": scenario_id,
                    "status": "aborted",
                    "attempts": attempt - 1,
                }
            try:
                trajectory = input_path.read()
                tools_info = trajectory.get("tools_info")
                if not isinstance(tools_info, list):
                    raise ValueError("trajectory does not contain a tools_info array")
                if args.attack_mode == "one_shot":
                    attack = run_one_shot_attack(
                        tools_info,
                        model,
                        args.provider,
                        args.temperature,
                        observation_format=args.observation_format,
                        request_fn=request_fn,
                    )
                elif reuse_stage1_dir is None:
                    checkpoint_path = stage1_dir / input_path.name
                    checkpoint: dict[str, Any] | None = None
                    if checkpoint_path.is_file():
                        try:
                            candidate = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                            candidate_metadata = candidate.get("metadata", {})
                            if (
                                isinstance(candidate.get("stage1"), dict)
                                and candidate_metadata.get("model") == model
                                and candidate_metadata.get("trajectory_sha256") == trajectory_sha256
                                and candidate_metadata.get("attacker_observation_format")
                                == args.observation_format
                            ):
                                checkpoint = candidate
                        except (OSError, json.JSONDecodeError):
                            checkpoint = None
                    if checkpoint is None:
                        stage1_output, observation_metadata, stage1_request = run_stage1_attack(
                            tools_info,
                            model,
                            args.provider,
                            args.temperature,
                            observation_format=args.observation_format,
                            request_fn=request_fn,
                        )
                        checkpoint = {
                            "stage1": stage1_output,
                            "metadata": {
                                **observation_metadata,
                                "model": model,
                                "trajectory_sha256": trajectory_sha256,
                                "request": stage1_request,
                            },
                        }
                        write_json(checkpoint_path, checkpoint)
                    source_metadata = checkpoint["metadata"]
                    stage1_output = validate_output(checkpoint["stage1"])
                    observation_metadata = {
                        key: source_metadata[key]
                        for key in (
                            "attacker_view_policy", "attacker_observation_format",
                            "raw_call_count", "visible_call_count",
                            "removed_cached_duplicate_count", "removed_failed_call_count",
                            "retained_empty_result_count", "retained_parameter_error_count",
                            "prior_equivalent", "raw_zero_call_prior_baseline",
                            "filtered_to_prior_equivalent", "observation_chars", "tool_catalog_count",
                        )
                        if key in source_metadata
                    }
                    attack = run_stage2_from_stage1(
                        stage1_output,
                        model,
                        args.provider,
                        args.temperature,
                        tool_call_count=source_metadata.get("visible_call_count"),
                        stage1_reused=True,
                        observation_metadata=observation_metadata,
                        request_fn=request_fn,
                    )
                    attack["metadata"]["requests"] = {
                        "stage1": source_metadata.get("request", {}),
                        **attack["metadata"].get("requests", {}),
                    }
                else:
                    stage1_source_path = reuse_stage1_dir / input_path.name
                    stage1_source = json.loads(
                        stage1_source_path.read_text(encoding="utf-8")
                    )
                    stage1_output = stage1_source.get("stage1")
                    if not isinstance(stage1_output, dict):
                        raise ValueError(
                            f"cached attack has no Stage 1 output: {stage1_source_path}"
                        )
                    source_metadata = stage1_source.get("metadata", {})
                    if (
                        source_metadata.get("attacker_view_policy")
                        != attacker_view_policy_for_format(args.observation_format)
                        or source_metadata.get("attacker_observation_format")
                        != args.observation_format
                    ):
                        raise ValueError(
                            "cached Stage 1 uses a different attacker-view representation"
                        )
                    observation_metadata = {
                        key: source_metadata[key]
                        for key in (
                            "attacker_view_policy",
                            "attacker_observation_format",
                            "raw_call_count",
                            "visible_call_count",
                            "removed_cached_duplicate_count",
                            "removed_failed_call_count",
                            "retained_empty_result_count",
                            "retained_parameter_error_count",
                            "prior_equivalent",
                            "raw_zero_call_prior_baseline",
                            "filtered_to_prior_equivalent",
                            "observation_chars",
                            "tool_catalog_count",
                        )
                        if key in source_metadata
                    }
                    attack = run_stage2_from_stage1(
                        stage1_output,
                        model,
                        args.provider,
                        args.temperature,
                        tool_call_count=source_metadata.get("tool_call_count"),
                        observation_metadata=observation_metadata,
                        request_fn=request_fn,
                    )
                    attack["metadata"]["stage1_source_path"] = str(
                        stage1_source_path
                    )
                attack["metadata"].update(
                    {
                        "scenario_id": trajectory.get("scenario_id") or scenario_id,
                        "profile_id": trajectory.get("profile_id"),
                        "domain": trajectory.get("domain"),
                        "trajectory_path": input_path.display_path,
                        "trajectory_sha256": trajectory_sha256,
                        "model": model,
                        "recovery_round": recovery_round,
                        "attempt": attempt,
                    }
                )
                if not is_usable_attack_value(
                    attack,
                    args.attack_mode,
                    args.observation_format,
                ):
                    raise ValueError("attack result failed completeness validation")
                write_json(output_path, attack)
                provider_health.record(False)
                print(
                    f"[{index}/{len(files)}] completed {scenario_id} attempts={attempt}",
                    flush=True,
                )
                return {
                    "scenario_id": scenario_id,
                    "status": "completed",
                    "attempts": attempt,
                    "recovery_round": recovery_round,
                    "output": portable_path(
                        output_path, relative_to=PROJECT_DIR
                    ),
                }
            except Exception as exc:
                last_error = str(exc)
                if is_fatal_provider_error(exc):
                    with fatal_error_lock:
                        if fatal_error["message"] is None:
                            fatal_error["message"] = last_error
                            print(
                                f"Fatal provider error; stopping batch: {last_error}",
                                flush=True,
                            )
                    stop_event.set()
                    return {
                        "scenario_id": scenario_id,
                        "status": "failed",
                        "attempts": attempt,
                        "recovery_round": recovery_round,
                        "error": last_error,
                    }
                if is_nonretryable_request_error(exc):
                    if args.materialize_exhausted:
                        from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES

                        try:
                            failure_trajectory = input_path.read()
                        except (OSError, ValueError, json.JSONDecodeError):
                            failure_trajectory = {}
                        failed_profile = {
                            attribute: {
                                "status": "failed", "evidence": [], "value": None,
                                "alternative_values": [],
                                "unresolved_reason": last_error,
                            }
                            for attribute in PROFILE_ATTRIBUTES
                        }
                        write_failed_stage1_checkpoint(
                            input_path,
                            trajectory_sha256,
                            failure_trajectory,
                            reason=last_error,
                            recovery_round=recovery_round,
                            attempt=attempt,
                        )
                        write_json(
                            output_path,
                            {
                                "profile": failed_profile,
                                "metadata": {
                                    "attack_type": args.attack_mode, "model": model,
                                    "scenario_id": failure_trajectory.get("scenario_id") or scenario_id,
                                    "profile_id": failure_trajectory.get("profile_id"),
                                    "domain": failure_trajectory.get("domain"),
                                    "trajectory_path": input_path.display_path,
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
                            "scenario_id": scenario_id,
                            "status": "materialized_failure",
                            "attempts": attempt,
                            "recovery_round": recovery_round,
                            "output": portable_path(
                                output_path, relative_to=PROJECT_DIR
                            ),
                        }
                    break
                provider_health.record(is_transient_provider_error(exc))
                if attempt <= args.retries:
                    print(
                        f"[{index}/{len(files)}] retry {scenario_id} "
                        f"attempt={attempt + 1}/{args.retries + 1}: {exc}",
                        flush=True,
                    )
                    if args.retry_delay:
                        delay = min(30.0, args.retry_delay * (2 ** (attempt - 1)))
                        time.sleep(delay * random.uniform(0.75, 1.25))
        # When this item has used its final recovery round, materialize the
        # fixed-denominator failure immediately.  Previously the runner wrote
        # the same artifact only on the *next* invocation, which forced one
        # more full input index/hash pass even though no request remained.
        if args.materialize_exhausted and recovery_round >= args.max_recovery_rounds:
            from privacy_trace.privacy_attacker import PROFILE_ATTRIBUTES

            try:
                failure_trajectory = input_path.read()
            except (OSError, ValueError, json.JSONDecodeError):
                failure_trajectory = {}
            failed_profile = {
                attribute: {
                    "status": "failed",
                    "evidence": [],
                    "value": None,
                    "alternative_values": [],
                    "unresolved_reason": last_error,
                }
                for attribute in PROFILE_ATTRIBUTES
            }
            write_failed_stage1_checkpoint(
                input_path,
                trajectory_sha256,
                failure_trajectory,
                reason=last_error,
                recovery_round=recovery_round,
                attempt=args.retries + 1,
            )
            write_json(
                output_path,
                {
                    "profile": failed_profile,
                    "metadata": {
                        "attack_type": args.attack_mode,
                        "model": model,
                        "scenario_id": (
                            failure_trajectory.get("scenario_id") or scenario_id
                        ),
                        "profile_id": failure_trajectory.get("profile_id"),
                        "domain": failure_trajectory.get("domain"),
                        "trajectory_path": input_path.display_path,
                        "trajectory_sha256": trajectory_sha256,
                        "attacker_view_policy": attacker_view_policy_for_format(
                            args.observation_format
                        ),
                        "attacker_observation_format": args.observation_format,
                        "failure_scored_as_incorrect": True,
                        "failure_reason": last_error,
                        "recovery_round": recovery_round,
                        "attempt": args.retries + 1,
                    },
                },
            )
            print(
                f"[{index}/{len(files)}] materialized failed {scenario_id}: "
                f"{last_error}",
                flush=True,
            )
            return {
                "scenario_id": scenario_id,
                "status": "materialized_failure",
                "attempts": args.retries + 1,
                "recovery_round": recovery_round,
                "output": portable_path(output_path, relative_to=PROJECT_DIR),
            }

        print(f"[{index}/{len(files)}] failed {scenario_id}: {last_error}", flush=True)
        return {
            "scenario_id": scenario_id,
            "status": "failed",
            "attempts": args.retries + 1,
            "recovery_round": recovery_round,
            "error": last_error,
        }

    def summary_value(completed_at: float | None = None) -> dict[str, Any]:
        ordered = sorted(
            results,
            key=lambda item: order[item["scenario_id"]],
        )
        return {
            "schema_version": "1.0",
            "attack_type": args.attack_mode,
            "reuse_stage1_dir": (
                str(reuse_stage1_dir) if reuse_stage1_dir is not None else None
            ),
            "model": model,
            "provider": args.provider,
            "api_key_pool_size": key_pool_size,
            "workers": args.workers,
            "started_at": started_at,
            "updated_at": time.time(),
            "completed_at": completed_at,
            "selected_count": len(files),
            "runnable_count": len(runnable),
            "finished_count": len(ordered),
            "completed_count": sum(item["status"] == "completed" for item in ordered),
            "failed_count": sum(item["status"] == "failed" for item in ordered),
            "aborted_count": sum(item["status"] == "aborted" for item in ordered),
            "skipped_count": sum(item["status"] == "skipped" for item in ordered),
            "materialized_failure_count": sum(
                item["status"] == "materialized_failure" for item in ordered
            ),
            "fatal_error": fatal_error["message"],
            "results": ordered,
        }

    order = {path.stem: index for index, path in enumerate(files)}

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    attack_one,
                    index,
                    input_path,
                    output_path,
                    recovery_round,
                    trajectory_sha256,
                ): index
                for (
                    index,
                    input_path,
                    output_path,
                    recovery_round,
                    trajectory_sha256,
                ) in runnable
            }
            for finished_index, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                results.append(result)
                scenario_id = str(result["scenario_id"])
                if result["status"] in {"completed", "materialized_failure"}:
                    ledger.resolve(scenario_id, metadata={"output": result.get("output")})
                else:
                    ledger.record_failure(
                        scenario_id,
                        error_type="privacy_attack",
                        error=str(result.get("error") or result["status"]),
                        attempts=int(result.get("attempts", 0)),
                        recovery_round=int(result.get("recovery_round", 0)),
                    )
                if finished_index % args.summary_every == 0:
                    write_json(output_dir / "_batch_summary.json", summary_value())
    finally:
        client.close()

    results.sort(key=lambda item: order[item["scenario_id"]])
    summary = summary_value(completed_at=time.time())
    write_json(output_dir / "_batch_summary.json", summary)
    recovery_manifest.update(
        {
            "status": "completed" if summary["failed_count"] == 0 else "failed",
            "updated_at": time.time(),
            "completed_at": time.time(),
            "unresolved_failure_count": len(ledger.unresolved()),
        }
    )
    write_json(recovery_root / "manifest.json", recovery_manifest)
    print(
        f"Attack batch complete: completed={summary['completed_count']} "
        f"failed={summary['failed_count']} skipped={summary['skipped_count']} "
        f"aborted={summary['aborted_count']} "
        f"-> {output_dir}"
    )
    if summary["fatal_error"]:
        return 42
    return 1 if summary["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
