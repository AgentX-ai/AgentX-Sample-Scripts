#!/usr/bin/env bash
# Runs the eval-ops suite in order and stops on the first failure.
# Env: AGENTX_API_KEY (any project key on the target engine), AGENTX_SELFHOST_BASE_URL.
set -euo pipefail
cd "$(dirname "$0")"
export AGENTX_EVAL_QUIET=1
for script in 01_splits_reuse_resume.py 02_review_label_calibration.py 03_eval_traffic_separation.py \
              04_judge_failure_trust.py 05_case_variance.py 06_dataset_lifecycle.py; do
  echo "=== $script"
  python3 "$script"
done
echo "=== 07_ts_ci_gate.mjs"
node 07_ts_ci_gate.mjs
echo "=== eval ops: all green"
