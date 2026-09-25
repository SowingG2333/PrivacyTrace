#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

output_root="artifacts/privacy_experiments/deepseek_v4_flash_0731/evaluation/judge_first"
batch_root="$output_root/batches"
log_dir="$output_root/logs"
session_name="agent_privacy_judge"
expected_count=2143

mkdir -p "$log_dir"
exec 9>"$output_root/.judge_scheduler.v2.lock"
if ! /usr/bin/flock -n 9; then
  exit 0
fi

now="$(date '+%Y-%m-%d %H:%M:%S %Z')"
completed_count="$(find "$batch_root" -type f -name '*.json' ! -name '._*' | wc -l)"
newest_result="$(
  find "$batch_root" -type f -name '*.json' ! -name '._*' -printf '%T@ %TY-%Tm-%Td %TH:%TM:%TS\n' \
    | sort -nr \
    | sed -n '1s/^[^ ]* //p'
)"
printf '[scheduler] time=%s completed=%s/%s newest=%s\n' \
  "$now" "$completed_count" "$expected_count" "${newest_result:-none}"

if (( completed_count >= expected_count )); then
  echo "[scheduler] all batch outputs are materialized; no runner needed"
  exit 0
fi

if /usr/bin/tmux has-session -t "$session_name" 2>/dev/null; then
  echo "[scheduler] session=$session_name is already running"
  exit 0
fi

run_stamp="$(date '+%Y%m%d_%H%M%S')"
run_log="$log_dir/recovery_daemon_${run_stamp}.log"
daemon="$project_dir/scripts/run_judge_recovery_daemon.sh"
tmux_command="exec \"$daemon\" >> \"$run_log\" 2>&1"
# Do not let a newly created tmux server inherit and hold the scheduler lock
# for the lifetime of the experiment.
/usr/bin/tmux new-session -d -s "$session_name" "$tmux_command" 9>&-
echo "[scheduler] started session=$session_name log=$run_log"
