#!/usr/bin/env bash
# Runs the eval deep-dive suite in order and stops on the first failure.
# Env: AGENTX_API_KEY (any project key on the target engine), AGENTX_SELFHOST_BASE_URL.
set -euo pipefail
cd "$(dirname "$0")"
export AGENTX_EVAL_QUIET=1
for script in 01_offline_lifecycle.py 02_grading_modes_and_analysis.py 03_agent_and_rag_checks.py \
              04_pairwise_and_pytest.py 05_online_scoring.py 06_judge_calibration_loop.py; do
  echo "=== $script"
  python3 "$script"
done
echo "=== eval deep dive: all green"
