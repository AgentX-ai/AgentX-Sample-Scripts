#!/usr/bin/env bash
# Runs the full buyer probe suite against $AGENTX_SELFHOST_BASE_URL / $AGENTX_API_KEY.
# UC1-6 + UC8 are pure-SDK Python; UC7/9/10 are deliberately curl-based ops probes.
# UC9 needs AGENTX_ADMIN_TOKEN; UC7/UC10 need AGENTX_ENGINE_DIR (skipped otherwise).
set -euo pipefail
mkdir -p results
for uc in uc1_instrument_support_agent uc2_offline_quality_gate uc3_online_scorers \
          uc4_ground_truth_calibration uc5_rag_faithfulness uc6_session_multiturn \
          uc8_backup_restore_drill; do
  echo "== $uc =="
  python3 "$uc.py" 2>&1 | grep -v "NotOpenSSL\|warnings.warn\|INFO -" | tee "results/${uc#uc?_}_rerun.txt" | tail -3
done
# uc9 runs BEFORE uc7: uc7 deliberately exhausts the credential rate-limit window (a 130-call
# 429 burst), which would otherwise turn every uc9 admin-route check into a 429.
if [ -n "${AGENTX_ADMIN_TOKEN:-}" ]; then
  echo "== uc9 (audit trail) =="
  bash uc9_audit_trail.sh | tee results/uc9_rerun.txt | tail -3
else
  echo "== uc9 skipped (set AGENTX_ADMIN_TOKEN) =="
fi
echo "== uc7 (ops) =="
bash uc7_operations.sh | tee results/uc7_rerun.txt | tail -3
if [ -n "${AGENTX_ENGINE_DIR:-}" ]; then
  echo "== uc10 (sso surface) =="
  bash uc10_sso_surface.sh | tee results/uc10_rerun.txt | tail -3
else
  echo "== uc10 skipped (set AGENTX_ENGINE_DIR) =="
fi
