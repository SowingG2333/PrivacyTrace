#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy it securely to $project_dir/.env and chmod 600 it." >&2
  exit 2
fi

set -a
source .env
set +a

export PRIVACY_ATTACK_API_KEY_INDEXES="${PRIVACY_ATTACK_API_KEY_INDEXES:-1,2,3}"
export MY_MODEL_API_KEY_RPMS="${MY_MODEL_API_KEY_RPMS:-500,100,1000}"
export MY_MODEL_API_KEY_MODELS="${MY_MODEL_API_KEY_MODELS:-glm-5.2,glm-5.2,glm5-2}"

exec ./.venv/bin/python scripts/run_semantic_judge.py \
  --records artifacts/privacy_experiments/deepseek_v4_flash_0731/evaluation/attack_records.jsonl \
  --output-root artifacts/privacy_experiments/deepseek_v4_flash_0731/evaluation/judge_first \
  --model glm5-2 \
  --equivalent-glm-pool \
  --temperature 0 \
  --workers 200 \
  --rpm 1600 \
  --batch-size 25 \
  --timeout 600 \
  --retries 5 \
  --retry-delay 0 \
  --max-recovery-rounds 5 \
  --materialize-exhausted \
  --resume
