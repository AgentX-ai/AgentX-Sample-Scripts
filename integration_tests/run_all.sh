#!/usr/bin/env bash
# Runs the integration tests in order and stops on the first failure.
# Env: AGENTX_API_KEY (any project key on the target engine), AGENTX_SELFHOST_BASE_URL.
set -euo pipefail
cd "$(dirname "$0")"
for script in 01_moveworks_mock_sync.py 02_databricks_mock_sync.py; do
  echo "=== $script"
  python3 "$script"
done
echo "=== integration tests: all green"
