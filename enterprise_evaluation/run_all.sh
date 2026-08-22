#!/usr/bin/env bash
# Runs UC1..UC6 in order against $AGENTX_SELFHOST_BASE_URL / $AGENTX_API_KEY, capturing output.
set -euo pipefail
mkdir -p results
for uc in uc1_instrument_support_agent uc2_offline_quality_gate uc3_online_scorers \
          uc4_ground_truth_calibration uc5_rag_faithfulness uc6_session_multiturn; do
  echo "== $uc =="
  python3 "$uc.py" 2>&1 | grep -v "NotOpenSSL\|warnings.warn\|INFO -" | tee "results/${uc#uc?_}_rerun.txt" | tail -3
done
echo "== uc7 (ops) =="
bash uc7_operations.sh | tee results/uc7_rerun.txt | tail -3
