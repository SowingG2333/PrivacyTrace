# Privacy experiment specification

## Prior-equivalent cohorts

The canonical 4,000-trajectory dataset contains 29 trajectories with no raw
tool calls. They form the observed zero-tool prior-baseline cohort and must not
be discarded from privacy evaluation:

```text
S0470 S0562 S0650 S0710 S1354 S1430 S1562 S1663 S1664 S1712
S1727 S1754 S1814 S2014 S2094 S2200 S2386 S2434 S2466 S2522
S2598 S2616 S2774 S2874 S3258 S3392 S3426 S3658 S3770
```

Attacker-view filtering currently leaves another 26 trajectories with zero
observable calls because every raw call was a cached repeat or a removable
failure. These records are tagged `filtered_to_prior_equivalent`; they remain a
separate stratum and are not silently merged into the 29 raw-zero records.

## Attacker-visible tool calls

The source trajectory remains immutable. Before constructing an attacker
prompt, the evaluation view applies this policy in original call order:

1. A cached repeat (`cache_hit`, `result_source=cache`, or an unexecuted
   duplicate occurrence) is removed. Only the first MCP execution is exposed.
2. Successful calls are retained.
3. Empty results are retained because an attempted query and its lack of a
   match are observable behavior.
4. Recognizable parameter or application-validation errors are retained because
   their arguments are user/Agent behavior and the error can reveal the query.
5. Transport errors, infrastructure failures, rate limits, unavailable servers,
   and `stalled_tool_loop` records are removed.

Each attack output records the raw and visible call counts, every removal and
retention count, and whether the record is raw-zero or became prior-equivalent
only after filtering. The policy identifier is
`first_execution_keep_success_empty_parameter_errors`.

## Attacker observation representation

Full-trajectory and channel attacks support two explicit, independently
resumable observation-format arms:

- `raw_repeated` is the original attack baseline. It exposes every raw
  `tools_info` record, including cached repeats and failed calls, and repeats
  each call's tool description and schema exactly as the original formatter
  did.
- `lossless_tool_catalog` is the treatment representation. It first applies the
  attacker-visible filtering policy above, then uses the deterministic,
  lossless tool catalog below.

The catalog representation applies these transformations:

1. Repeated tool descriptions and JSON schemas are emitted once in a tool
   catalog.
2. Ordered events refer to their catalog entry and retain every visible call's
   complete arguments, complete returned result, and original order.
3. JSON is serialized compactly without changing values.
4. There is no character threshold, result truncation, schema truncation, or
   model-generated summary.

Each attack output records its `attacker_observation_format`, view policy,
emitted observation character count, and number of catalog entries. Recovery
contracts include the format, so a baseline output can never be reused as a
catalog output or vice versa.

Example full-attack commands:

```bash
python scripts/run_privacy_attacks.py \
  --input artifacts/trajectories/trajectories.jsonl \
  --output-dir artifacts/privacy_attacks/raw_repeated \
  --attack-mode one_shot \
  --observation-format raw_repeated

python scripts/run_privacy_attacks.py \
  --input artifacts/trajectories/trajectories.jsonl \
  --output-dir artifacts/privacy_attacks/lossless_tool_catalog \
  --attack-mode one_shot \
  --observation-format lossless_tool_catalog
```
