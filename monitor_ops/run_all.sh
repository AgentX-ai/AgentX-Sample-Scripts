#!/usr/bin/env bash
# Runs the monitor-ops suite in order and stops on the first failure.
# Env: AGENTX_API_KEY (any project key on the target engine), AGENTX_SELFHOST_BASE_URL.
# 06 self-skips unless AGENTX_EXPECT_ONLINE_JUDGE_CAP is set (see README).
set -euo pipefail
cd "$(dirname "$0")"
export AGENTX_EVAL_QUIET=1
for script in 01_rules_route_traffic.py 02_custom_scorers.py 03_webhooks.py 04_topics.py \
              05_session_judge.py 06_online_judge_cap.py 07_otel_ingest_scoring.py; do
  echo "=== $script"
  python3 "$script"
done
echo "=== monitor ops: all green"
