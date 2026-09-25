#!/usr/bin/env python3
"""Run cached, batched LLM-as-Judge evaluation for unresolved semantic matches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import threading
import time
from collections import Counter, defaultdict
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
    read_json_object,
    require_model,
)

from privacy_trace.hybrid_judge import (  # noqa: E402
    build_adjudication_prompt,
    build_judge_prompt,
    build_verification_prompt,
    parse_judge_response,
    unique_cases,
)
from privacy_trace.llm_client import DirectOpenAIClient  # noqa: E402


@dataclass(frozen=True)
class JudgeBatch:
    batch_id: str
    attribute: str
    cases: tuple[dict[str, Any], ...]
    output_path: Path


def read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected object at {path}:{line_number}")
        records.append(value)
    if not records:
        raise ValueError(f"No records found in {path}")
    return records


def write_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def build_batches(
    cases: list[dict[str, Any]],
    output_root: Path,
    batch_size: int,
) -> list[JudgeBatch]:
    by_attribute: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_attribute[str(case["attribute"])].append(case)

    batches: list[JudgeBatch] = []
    for attribute, items in sorted(by_attribute.items()):
        for offset in range(0, len(items), batch_size):
            batch_cases = tuple(items[offset : offset + batch_size])
            digest = hashlib.sha256(
                json.dumps(
                    [case["case_id"] for case in batch_cases],
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:12]
            index = offset // batch_size + 1
            batch_id = f"{attribute}-{index:05d}-{digest}"
            batches.append(
                JudgeBatch(
                    batch_id=batch_id,
                    attribute=attribute,
                    cases=batch_cases,
                    output_path=output_root
                    / "batches"
                    / attribute
                    / f"{batch_id}.json",
                )
            )
    return batches


def is_usable_output(
    path: Path, batch: JudgeBatch, *, model: str | tuple[str, ...] | None = None
) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict) or value.get("batch_id") != batch.batch_id:
        return False
    allowed_models = (model,) if isinstance(model, str) else model
    if allowed_models is not None and value.get("model") not in allowed_models:
        return False
    judgments = value.get("judgments")
    if not isinstance(judgments, list):
        return False
    expected = {case["case_id"] for case in batch.cases}
    actual = {
        str(item.get("case_id"))
        for item in judgments
        if isinstance(item, dict)
        and item.get("verdict") in {"correct", "incorrect", "uncertain"}
    }
    return actual == expected and len(judgments) == len(expected)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--verify-judge-root",
        type=Path,
        help="Conservatively rejudge only first-pass cases marked correct.",
    )
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="Use final false-positive challenge prompt for selected positives.",
    )
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--rpm", type=int, default=500)
    parser.add_argument("--limit-batches", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--max-recovery-rounds", type=int, default=5)
    parser.add_argument("--materialize-exhausted", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--model",
        help="Judge model ID override; defaults to MY_MODEL_NAME.",
    )
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--equivalent-glm-pool", action="store_true", help="Route equivalent GLM Judge IDs per configured key slot.")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--summary-every", type=int, default=20)
    return parser


def first_pass_correct_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    paths = sorted((path / "batches").rglob("*.json"))
    if not paths:
        manifest = read_json_object(path / "_manifest.json") or {}
        if int(manifest.get("unique_case_count", -1)) == 0:
            return set()
        raise ValueError(f"No first-pass judge outputs found in {path}")
    for batch_path in paths:
        value = json.loads(batch_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(
            value.get("judgments"), list
        ):
            raise ValueError(f"Invalid first-pass judge output: {batch_path}")
        for item in value["judgments"]:
            if isinstance(item, dict) and item.get("verdict") == "correct":
                ids.add(str(item.get("case_id") or ""))
    ids.discard("")
    return ids


def main() -> int:
    from agent_env.settings import load_project_env

    load_project_env(PROJECT_DIR)
    args = build_arg_parser().parse_args()
    if args.max_tokens is not None:
        print(
            "Error: Privacy Attack V1 uses the service-default output limit; "
            "omit --max-tokens.",
            file=sys.stderr,
        )
        return 2
    if (
        args.batch_size <= 0
        or args.workers <= 0
        or args.retries < 0
        or args.retry_delay < 0
        or args.max_recovery_rounds < 0
        or args.rpm <= 0
    ):
        print("Error: invalid batch or retry settings", file=sys.stderr)
        return 2
    if args.limit_batches is not None and args.limit_batches <= 0:
        print("Error: --limit-batches must be positive", file=sys.stderr)
        return 2
    if args.adversarial and args.verify_judge_root is None:
        print("Error: --adversarial requires --verify-judge-root", file=sys.stderr)
        return 2

    records_path = args.records.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        records = read_records(records_path)
        all_cases = unique_cases(records)
        verification_source = (
            args.verify_judge_root.resolve()
            if args.verify_judge_root is not None
            else None
        )
        if verification_source is None:
            cases = all_cases
        else:
            selected_ids = first_pass_correct_ids(verification_source)
            cases = [
                case for case in all_cases if case["case_id"] in selected_ids
            ]
            missing_selected = selected_ids - {
                case["case_id"] for case in cases
            }
            if missing_selected:
                raise ValueError(
                    f"First-pass correct cases missing from records: "
                    f"{len(missing_selected)}"
                )
        batches = build_batches(cases, output_root, args.batch_size)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    all_batches = batches
    if args.limit_batches is not None:
        batches = batches[: args.limit_batches]

    model = args.model or os.environ.get("MY_MODEL_NAME")
    base_url = os.environ.get("MY_MODEL_BASE_URL")
    try:
        key_pool_size = DirectOpenAIClient.environment_key_count()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not model or not base_url or key_pool_size <= 0:
        print(
            "Error: MY_MODEL_NAME, MY_MODEL_BASE_URL, and an enabled model API key are required",
            file=sys.stderr,
        )
        return 2
    try:
        require_model(model, stage="semantic judge", expected_model="glm5-2")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    experiment = (
        "hybrid_llm_judge_adjudication"
        if args.adversarial
        else (
            "hybrid_llm_judge_verification"
            if verification_source is not None
            else "hybrid_llm_judge"
        )
    )

    run_id = args.run_id or output_root.name
    recovery_root = output_root / "_recovery"
    contract_inputs = [records_path]
    if verification_source is not None:
        # Only prior-round case outputs determine this round's selected IDs.
        # Runtime summaries contain timestamps/worker counts and must not make a
        # lower-concurrency resume checkpoint-incompatible.
        contract_inputs.extend(
            sorted((verification_source / "batches").rglob("*.json"))
        )
    try:
        recovery_manifest = ensure_recovery_manifest(
            recovery_root / "manifest.json",
            RecoveryContract(
                run_id=run_id,
                phase=experiment,
                model=model,
                prompt_version="semantic_asr_three_round_consensus",
                seed=None,
                input_hashes=file_hashes(
                    contract_inputs, relative_to=PROJECT_DIR
                ),
                config={
                    "batch_size": args.batch_size,
                    "limit_batches": args.limit_batches,
                    "temperature": args.temperature,
                    "thinking_mode": "disabled",
                    "equivalent_glm_pool": args.equivalent_glm_pool,
                    "rpm": args.rpm,
                    "max_tokens": None,
                    "output_limit_source": "service_default",
                    "direct_transport": "trust_env_false",
                    "api_key_pool_size": key_pool_size,
                    "verification_source": (
                        portable_path(
                            verification_source, relative_to=PROJECT_DIR
                        )
                        if verification_source
                        else None
                    ),
                    "adversarial": args.adversarial,
                },
                required_model="glm5-2",
            ),
            workers=args.workers,
            resume=args.resume,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    ledger = FailureLedger(
        recovery_root, phase=experiment, run_id=run_id
    )

    source_sha256 = hashlib.sha256(records_path.read_bytes()).hexdigest()
    case_counts = Counter(case["attribute"] for case in cases)
    manifest = {
        "schema_version": "1.0",
        "experiment": experiment,
        "model": model,
        "base_url": base_url,
        "source_records": portable_path(
            records_path, relative_to=PROJECT_DIR
        ),
        "source_sha256": source_sha256,
        "record_count": len(records),
        "all_semantic_case_count": len(all_cases),
        "unique_case_count": len(cases),
        "verification_source": (
            portable_path(verification_source, relative_to=PROJECT_DIR)
            if verification_source is not None
            else None
        ),
        "case_counts_by_attribute": dict(sorted(case_counts.items())),
        "batch_size": args.batch_size,
        "full_batch_count": len(all_batches),
        "selected_batch_count": len(batches),
        "workers": args.workers,
        "api_key_pool_size": key_pool_size,
    }
    write_json(output_root / "_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0

    runnable: list[tuple[JudgeBatch, int]] = []
    results: list[dict[str, Any]] = []
    for batch in batches:
        if args.resume and is_usable_output(
            batch.output_path,
            batch,
            model=("glm-5.2", "glm5-2") if args.equivalent_glm_pool else model,
        ):
            results.append(
                {
                    "batch_id": batch.batch_id,
                    "attribute": batch.attribute,
                    "status": "skipped",
                    "case_count": len(batch.cases),
                }
            )
            ledger.resolve(
                batch.batch_id,
                metadata={
                    "output": portable_path(
                        batch.output_path, relative_to=PROJECT_DIR
                    )
                },
            )
        else:
            recovery_round = ledger.recovery_round(
                batch.batch_id, resume=args.resume
            )
            if recovery_round > args.max_recovery_rounds:
                if args.materialize_exhausted:
                    write_json(
                        batch.output_path,
                        {
                            "schema_version": "1.0",
                            "experiment": manifest["experiment"],
                            "batch_id": batch.batch_id,
                            "attribute": batch.attribute,
                            "model": model,
                            "temperature": args.temperature,
                            "cases": list(batch.cases),
                            "judgments": [
                                {
                                    "case_id": case["case_id"],
                                    "verdict": "uncertain",
                                    "relation": "insufficient",
                                    "confidence": 0.0,
                                    "reason": "judge recovery exhausted",
                                }
                                for case in batch.cases
                            ],
                            "metadata": {
                                "failure_scored_as_incorrect": True,
                                "recovery_round": recovery_round,
                            },
                        },
                    )
                    ledger.resolve(
                        batch.batch_id,
                        metadata={
                            "output": portable_path(
                                batch.output_path, relative_to=PROJECT_DIR
                            ),
                            "materialized_failure": True,
                        },
                    )
                    results.append(
                        {
                            "batch_id": batch.batch_id,
                            "attribute": batch.attribute,
                            "status": "materialized_failure",
                            "case_count": len(batch.cases),
                            "recovery_round": recovery_round,
                        }
                    )
                    continue
                results.append(
                    {
                        "batch_id": batch.batch_id,
                        "attribute": batch.attribute,
                        "status": "failed",
                        "case_count": len(batch.cases),
                        "attempts": 0,
                        "error": "maximum recovery rounds exhausted",
                        "recovery_round": recovery_round,
                    }
                )
                continue
            if args.resume and batch.output_path.exists():
                quarantine_artifact(
                    batch.output_path,
                    recovery_root / "quarantine",
                    item_id=batch.batch_id,
                    reason="unusable_judge_output",
                )
            runnable.append((batch, recovery_round))

    client = DirectOpenAIClient.from_environment(
        timeout=args.timeout,
        max_connections=min(args.workers, 200),
        use_environment_key_models=args.equivalent_glm_pool,
    )
    stop_event = threading.Event()
    fatal_error: dict[str, str | None] = {"message": None}
    fatal_lock = threading.Lock()
    rate_limiter = RollingRateLimiter(args.rpm)
    provider_health = RollingFailureWindow()
    started_at = time.time()

    def execute(batch: JudgeBatch, recovery_round: int) -> dict[str, Any]:
        if stop_event.is_set():
            return {
                "batch_id": batch.batch_id,
                "attribute": batch.attribute,
                "status": "aborted",
                "case_count": len(batch.cases),
            }
        if args.adversarial:
            prompt = build_adjudication_prompt(
                batch.attribute, list(batch.cases)
            )
        elif verification_source is not None:
            prompt = build_verification_prompt(
                batch.attribute, list(batch.cases)
            )
        else:
            prompt = build_judge_prompt(batch.attribute, list(batch.cases))
        expected_ids = {case["case_id"] for case in batch.cases}
        last_error = "unknown error"
        for attempt in range(1, args.retries + 2):
            try:
                response = client.complete_json(
                    prompt=prompt,
                    model=model,
                    temperature=args.temperature,
                    stage=experiment,
                    limiter=rate_limiter,
                    thinking_mode="disabled",
                )
                if response.metadata.get("finish_reason") == "length":
                    raise RuntimeError("response truncated: finish_reason=length")
                raw_output = response.text
                judgments = parse_judge_response(raw_output, expected_ids)
                result = {
                    "schema_version": "1.0",
                    "experiment": manifest["experiment"],
                    "batch_id": batch.batch_id,
                    "attribute": batch.attribute,
                    "model": response.metadata.get("model", model),
                    "temperature": args.temperature,
                    "thinking_mode": "disabled",
                    "prompt_sha256": hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest(),
                    "cases": list(batch.cases),
                    "judgments": judgments,
                    "metadata": {
                        "recovery_round": recovery_round,
                        "attempt": attempt,
                        "request": response.metadata,
                    },
                }
                write_json(batch.output_path, result)
                provider_health.record(False)
                return {
                    "batch_id": batch.batch_id,
                    "attribute": batch.attribute,
                    "status": "completed",
                    "case_count": len(batch.cases),
                    "attempts": attempt,
                    "recovery_round": recovery_round,
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
                        write_json(
                            batch.output_path,
                            {
                                "schema_version": "1.0",
                                "experiment": manifest["experiment"],
                                "batch_id": batch.batch_id,
                                "attribute": batch.attribute,
                                "model": model,
                                "temperature": args.temperature,
                                "cases": list(batch.cases),
                                "judgments": [
                                    {
                                        "case_id": case["case_id"],
                                        "verdict": "uncertain",
                                        "relation": "insufficient",
                                        "confidence": 0.0,
                                        "reason": last_error,
                                    }
                                    for case in batch.cases
                                ],
                                "metadata": {
                                    "failure_scored_as_incorrect": True,
                                    "failure_reason": last_error,
                                    "recovery_round": recovery_round,
                                    "attempt": attempt,
                                },
                            },
                        )
                        return {
                            "batch_id": batch.batch_id,
                            "attribute": batch.attribute,
                            "status": "materialized_failure",
                            "case_count": len(batch.cases),
                            "attempts": attempt,
                            "recovery_round": recovery_round,
                            "output": portable_path(
                                batch.output_path, relative_to=PROJECT_DIR
                            ),
                        }
                    break
                provider_health.record(is_transient_provider_error(exc))
                if attempt <= args.retries and args.retry_delay > 0:
                    delay = min(30.0, args.retry_delay * (2 ** (attempt - 1)))
                    time.sleep(delay * random.uniform(0.75, 1.25))
        return {
            "batch_id": batch.batch_id,
            "attribute": batch.attribute,
            "status": "failed",
            "case_count": len(batch.cases),
            "attempts": args.retries + 1,
            "recovery_round": recovery_round,
            "error": last_error,
        }

    def summary_value(completed_at: float | None = None) -> dict[str, Any]:
        statuses = Counter(item["status"] for item in results)
        cases_by_status: Counter[str] = Counter()
        for item in results:
            cases_by_status[item["status"]] += int(item["case_count"])
        failures = [
            {
                "batch_id": item.get("batch_id"),
                "attribute": item.get("attribute"),
                "case_count": item.get("case_count"),
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
            "runnable_batch_count": len(runnable),
            "finished_batch_count": len(results),
            "batch_statuses": dict(statuses),
            "case_statuses": dict(cases_by_status),
            "failure_count": len(failures),
            "materialized_failure_count": sum(
                item["status"] == "materialized_failure" for item in results
            ),
            "failure_examples": failures[:20],
            "fatal_error": fatal_error["message"],
        }

    print(
        f"Starting {len(runnable)} judge batches with workers={args.workers}; "
        f"resume_skips={len(batches) - len(runnable)}",
        flush=True,
    )
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(execute, batch, recovery_round): batch
                for batch, recovery_round in runnable
            }
            for finished_index, future in enumerate(as_completed(futures), start=1):
                item = future.result()
                results.append(item)
                batch_id = str(item["batch_id"])
                if item["status"] in {"completed", "materialized_failure"}:
                    ledger.resolve(batch_id, metadata={"output": item.get("output")})
                else:
                    ledger.record_failure(
                        batch_id,
                        error_type="semantic_judge",
                        error=str(item.get("error") or item["status"]),
                        attempts=int(item.get("attempts", 0)),
                        recovery_round=int(item.get("recovery_round", 0)),
                        metadata={"attribute": item.get("attribute")},
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
                    judged_cases = sum(
                        int(result["case_count"])
                        for result in results
                        if result["status"] in {"completed", "skipped"}
                    )
                    print(
                        f"progress={finished_index}/{len(runnable)} "
                        f"completed={completed} failed={failed} "
                        f"judged_cases={judged_cases} "
                        f"throughput={finished_index / elapsed:.2f} batches/s",
                        flush=True,
                    )
                    write_json(
                        output_root / "_batch_summary.json", summary_value()
                    )
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
    return 1 if summary["batch_statuses"].get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
