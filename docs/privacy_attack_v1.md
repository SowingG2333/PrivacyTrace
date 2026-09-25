# Privacy Attack V1 execution contract

All attacker arms use `deepseek-v4-flash-0731`, `--temperature 1.0`,
`--thinking-mode disabled`, `--timeout 600`, and `--rpm 500`. The normal
setting is `--workers 100`; the active E4 single-server run uses 200 workers
and a 200-connection shared pool as an authorized throughput trial.
All Judge arms use `glm-5.2`, `--temperature 0`, `--timeout 600`, `--workers 100`,
and `--rpm 500`. Do not pass `--max-tokens`: the service controls output limits.
All API calls use a direct HTTP transport (`trust_env=False`), so environment
proxy variables are never consulted.
The runner round-robins the slots selected by `PRIVACY_ATTACK_API_KEY_INDEXES`
from `MY_MODEL_API_KEY` and `MY_MODEL_API_KEYS` (the workspace selects `1,2`,
including key2), subject to the global command RPM and each configured
`MY_MODEL_API_KEY_RPMS` limit; keys themselves are never saved.

Every full run must also pass `--retries 5 --max-recovery-rounds 5`
`--materialize-exhausted --resume`. Repeat the same command with `--resume` for
at most five recovery rounds when `_batch_summary.json` reports retryable work.
If an API connection stops making progress, supervise the same resumable command
with `run_privacy_recovery_watchdog.py`. It restarts only the stalled process,
thereby creating a fresh `trust_env=False` HTTP pool while preserving the
already-atomic result files. It never introduces a proxy or changes the request
parameters:

```bash
python3 scripts/run_privacy_recovery_watchdog.py \
  --output-dir artifacts/privacy_experiments/deepseek_v4_flash_0731/schema_prior/schema_only \
  --expected-count 4000 --stall-seconds 150 -- \
  python3 scripts/run_channel_experiments.py ... --resume
```

The official output root is `artifacts/privacy_experiments/deepseek_v4_flash_0731/`.
Run schema prior, catalog one-shot, catalog two-stage, raw one-shot,
single-server, and cross-domain in that order. Use `--limit 100` and a separate
`_smoke` output root before a full run.

Cross-domain invocation:

```bash
python3 scripts/run_view_scope_experiments.py \
  --input artifacts/trajectories/trajectories.jsonl \
  --output-root artifacts/privacy_experiments/deepseek_v4_flash_0731/view_scope/cross_domain_all_servers \
  --scope cross_domain_all_servers --observation-format lossless_tool_catalog \
  --model deepseek-v4-flash-0731 --temperature 1.0 --thinking-mode disabled \
  --workers 100 --rpm 500 --timeout 600 --retries 5 \
  --max-recovery-rounds 5 --materialize-exhausted --resume
```

Build records with normal arms for schema/catalog/raw/cross-domain, a recursive
arm for `server_view`, and a two-stage arm for the two-stage directory. The
record builder always emits all 17 attributes per generated view and keeps
failed views in the denominator.
