#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -f .env ]]; then
  echo "Missing $project_dir/.env" >&2
  exit 2
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "Missing $project_dir/.venv/bin/python" >&2
  exit 2
fi

set -a
source .env
set +a

# Preserve the recovery contract used by judge_first. The checked-in .env is
# also used by attacker runs, whose two-slot defaults are checkpoint-incompatible
# with this three-slot equivalent-GLM Judge pool.
export PRIVACY_ATTACK_API_KEY_INDEXES="1,2,3"
export MY_MODEL_API_KEY_RPMS="500,100,1000"
export MY_MODEL_API_KEY_MODELS="glm-5.2,glm-5.2,glm5-2"

output_root="artifacts/privacy_experiments/deepseek_v4_flash_0731/evaluation/judge_first"

exec ./.venv/bin/python scripts/run_privacy_recovery_watchdog.py \
  --output-dir "$output_root/batches" \
  --expected-count 2143 \
  --pattern '**/[!._]*.json' \
  --stall-seconds 360 \
  --poll-seconds 1 \
  --restart-delay 0 \
  -- \
  ./.venv/bin/python scripts/run_semantic_judge.py \
    --records artifacts/privacy_experiments/deepseek_v4_flash_0731/evaluation/attack_records.jsonl \
    --output-root "$output_root" \
    --model glm5-2 \
    --equivalent-glm-pool \
    --temperature 0 \
    --workers 200 \
    --rpm 1600 \
    --batch-size 25 \
    --timeout 300 \
    --retries 0 \
    --retry-delay 0 \
    --max-recovery-rounds 5 \
    --materialize-exhausted \
    --resume
