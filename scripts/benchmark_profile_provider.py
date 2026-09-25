#!/usr/bin/env python3
"""Benchmark Profile seed-audit payloads without exposing provider keys."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import statistics
import time
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from privacy_trace.profile_generator import (
    GeneratorConfig,
    SyntheticProfileGenerator,
    build_seed_audit_prompt,
    parse_json_output,
)


@dataclass
class RequestResult:
    success: bool
    latency_seconds: float
    candidate_count: int
    error: str = ""


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def parse_positive_ints(raw: str, option: str) -> list[int]:
    try:
        values = [int(value) for value in raw.split(",") if value.strip()]
    except ValueError as exc:
        raise SystemExit(f"{option} must contain comma-separated integers") from exc
    if not values or any(value <= 0 for value in values):
        raise SystemExit(f"{option} values must be positive")
    return values


def configured_slots() -> tuple[list[str], list[str]]:
    primary = os.environ.get("MY_MODEL_API_KEY", "").strip()
    extras = [
        value.strip()
        for value in os.environ.get("MY_MODEL_API_KEYS", "").split(",")
        if value.strip()
    ]
    keys: list[str] = []
    for value in [primary, *extras]:
        if value and value not in keys:
            keys.append(value)
    if not keys:
        raise SystemExit("No MY_MODEL_API_KEY(S) are configured")
    raw_models = [
        value.strip()
        for value in os.environ.get("MY_MODEL_API_KEY_MODELS", "").split(",")
        if value.strip()
    ]
    default_model = os.environ.get("MY_MODEL_NAME", "glm-5.2").strip()
    models = raw_models or [default_model] * len(keys)
    if len(models) != len(keys):
        raise SystemExit("MY_MODEL_API_KEY_MODELS must match the deduplicated key count")
    return keys, models


def representative_seeds(count: int) -> list[dict[str, Any]]:
    generator = SyntheticProfileGenerator(
        GeneratorConfig(seed=42, candidate_count=count, skip_ipf=True),
        targets=[],
    )
    return generator.generate_candidates(count)


def run_request(
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout: float,
    base_seeds: list[dict[str, Any]],
    request_index: int,
) -> RequestResult:
    candidates = copy.deepcopy(base_seeds)
    expected_ids = set()
    for item_index, candidate in enumerate(candidates):
        candidate_id = f"benchmark_{request_index:08d}_{item_index:03d}"
        candidate["_candidate_id"] = candidate_id
        expected_ids.add(candidate_id)
    prompt = build_seed_audit_prompt(candidates)
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        max_retries=0,
    )
    started = time.monotonic()
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        if model.casefold() in {"glm-5.2", "glm5-2"}:
            kwargs["reasoning_effort"] = "none"
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        parsed = parse_json_output(content or "")
        results = parsed.get("results")
        if not isinstance(results, list):
            raise RuntimeError("missing results array")
        actual_ids = {
            str(item.get("candidate_id"))
            for item in results
            if isinstance(item, dict) and item.get("candidate_id") is not None
        }
        if actual_ids != expected_ids or len(results) != len(expected_ids):
            raise RuntimeError(
                f"candidate mismatch expected={len(expected_ids)} "
                f"actual={len(actual_ids)} items={len(results)}"
            )
        return RequestResult(
            success=True,
            latency_seconds=time.monotonic() - started,
            candidate_count=len(candidates),
        )
    except Exception as exc:
        return RequestResult(
            success=False,
            latency_seconds=time.monotonic() - started,
            candidate_count=len(candidates),
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
    finally:
        client.close()


def summarize(
    *,
    key_alias: str,
    model: str,
    batch_size: int,
    concurrency: int,
    wall_seconds: float,
    results: list[RequestResult],
) -> dict[str, Any]:
    successful = [result for result in results if result.success]
    latencies = [result.latency_seconds for result in successful]
    errors: dict[str, int] = {}
    for result in results:
        if not result.success:
            category = result.error.split(":", 1)[0]
            errors[category] = errors.get(category, 0) + 1
    success_count = len(successful)
    return {
        "key": key_alias,
        "model": model,
        "batch_size": batch_size,
        "concurrency": concurrency,
        "requests": len(results),
        "successes": success_count,
        "failures": len(results) - success_count,
        "success_rate": success_count / len(results),
        "wall_seconds": wall_seconds,
        "latency_p50_seconds": statistics.median(latencies) if latencies else None,
        "latency_p95_seconds": percentile(latencies, 0.95),
        "successful_request_rpm": success_count * 60.0 / wall_seconds,
        "successful_sample_rpm": (
            success_count * batch_size * 60.0 / wall_seconds
        ),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-sizes", default="5,10,25,50")
    parser.add_argument("--concurrencies", default="1,5,10,20")
    parser.add_argument("--waves", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--cooldown", type=float, default=2.0)
    parser.add_argument("--key-indexes", default="1,2,3")
    parser.add_argument("--stop-failure-rate", type=float, default=0.5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.waves <= 0 or args.timeout <= 0 or args.cooldown < 0:
        raise SystemExit("waves/timeout must be positive and cooldown non-negative")

    load_dotenv()
    base_url = os.environ.get("MY_MODEL_BASE_URL", "").strip()
    if not base_url:
        raise SystemExit("MY_MODEL_BASE_URL is not configured")
    keys, models = configured_slots()
    key_indexes = parse_positive_ints(args.key_indexes, "--key-indexes")
    if any(index > len(keys) for index in key_indexes):
        raise SystemExit("--key-indexes references an unavailable key")
    batch_sizes = parse_positive_ints(args.batch_sizes, "--batch-sizes")
    concurrencies = parse_positive_ints(args.concurrencies, "--concurrencies")
    base_candidates = representative_seeds(max(batch_sizes))

    output: dict[str, Any] = {
        "schema_version": 1,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url_host": base_url.split("//", 1)[-1].split("/", 1)[0],
        "timeout_seconds": args.timeout,
        "waves": args.waves,
        "results": [],
    }
    request_serial = 0
    for batch_size in batch_sizes:
        seeds = base_candidates[:batch_size]
        for key_number in key_indexes:
            key_index = key_number - 1
            stop_higher_concurrency = False
            for concurrency in concurrencies:
                if stop_higher_concurrency:
                    break
                total_requests = concurrency * args.waves
                started = time.monotonic()
                results: list[RequestResult] = []
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = []
                    for _ in range(total_requests):
                        request_serial += 1
                        futures.append(
                            executor.submit(
                                run_request,
                                api_key=keys[key_index],
                                model=models[key_index],
                                base_url=base_url,
                                timeout=args.timeout,
                                base_seeds=seeds,
                                request_index=request_serial,
                            )
                        )
                    for future in as_completed(futures):
                        results.append(future.result())
                summary = summarize(
                    key_alias=f"key_{key_number}",
                    model=models[key_index],
                    batch_size=batch_size,
                    concurrency=concurrency,
                    wall_seconds=time.monotonic() - started,
                    results=results,
                )
                output["results"].append(summary)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                temporary = args.output.with_suffix(args.output.suffix + ".tmp")
                temporary.write_text(
                    json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, args.output)
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                stop_higher_concurrency = (
                    summary["failures"] > 0
                    and 1.0 - summary["success_rate"] >= args.stop_failure_rate
                )
                if args.cooldown:
                    time.sleep(args.cooldown)
    output["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
