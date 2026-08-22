#!/usr/bin/env bash
# UC9 (round 2) - Audit trail probe. Deliberately curl-based like UC7: the audit read is an
# OPERATOR surface (x-admin-token), not an SDK one, by design.
#
# Round-1 finding this probes: [GAP] "no audit log" (FINDINGS.md UC7).
# Checks: config mutations land as rows with honest actors; secrets never land; the data plane
# stays out; and the trail has no mutation surface at all.
#
#   AGENTX_ADMIN_TOKEN=... bash uc9_audit_trail.sh
set -uo pipefail
BASE=${AGENTX_SELFHOST_BASE_URL:-http://localhost:4791/api/v1}
TOKEN=${AGENTX_ADMIN_TOKEN:?"set AGENTX_ADMIN_TOKEN to the operator token"}

echo "=== 9.1 a scorer's lifecycle lands as audit rows ==="
KEY=$(curl -s -X POST "$BASE/projects" -H 'content-type: application/json' \
  -d '{"name":"uc9-audit"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['project']['apiKey'])")
SCORER=$(curl -s -X POST "$BASE/agent-monitoring/custom-evaluators" -H "x-api-key: $KEY" \
  -H 'content-type: application/json' \
  -d '{"name":"UC9 probe","kind":"code","language":"python","sampleRate":1,"alertBelow":0.5,"script":"async def handler(input, output, expected, metadata, trace):\n    return 1.0  # SECRET-MARKER-UC9\n"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['evaluator']['_id'])")
curl -s -X DELETE "$BASE/agent-monitoring/custom-evaluators/$SCORER" -H "x-api-key: $KEY" > /dev/null
for i in 1 2 3; do
  curl -s -X POST "$BASE/ingest/traces" -H "x-api-key: $KEY" -H 'content-type: application/json' \
    -d "{\"name\":\"uc9-noise\",\"input\":\"q$i\",\"output\":\"a\"}" > /dev/null
done
curl -s "$BASE/export/signals" -H "x-api-key: $KEY" > /dev/null

AUDIT=$(curl -s "$BASE/admin/audit?limit=500" -H "x-admin-token: $TOKEN")
UC9_AUDIT="$AUDIT" python3 - "$SCORER" <<'PY'
import json, os, sys
scorer_id = sys.argv[1]
events = json.loads(os.environ["UC9_AUDIT"])["events"]
creates = [e for e in events if e["action"] == "scorer.create"]
deletes = [e for e in events if e["action"] == "scorer.delete" and e.get("entityId") == scorer_id]
ingest = [e for e in events if "/ingest/" in e["path"]]
exports = [e for e in events if e["action"] == "export.read"]
blob = json.dumps(events)
print(f"scorer.create rows: {len(creates)} (expect >=1), scorer.delete for our id: {len(deletes)} (expect 1)")
print(f"actor on create: {creates[0]['actor'] if creates else '-'} (expect project:<id>)")
print(f"script content leaked into trail: {'SECRET-MARKER-UC9' in blob} (expect False)")
print(f"data-plane ingest rows: {len(ingest)} (expect 0); export.read rows: {len(exports)} (expect >=1)")
PY

echo
echo "=== 9.2 reads are gated, and the trail is immutable ==="
NOAUTH=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/admin/audit")
WRONG=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/admin/audit" -H "x-admin-token: nope")
echo "read without token: HTTP $NOAUTH; wrong token: HTTP $WRONG (expect 401/401)"
for M in PUT PATCH DELETE POST; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X "$M" "$BASE/admin/audit" -H "x-admin-token: $TOKEN")
  echo "  $M /admin/audit: HTTP $CODE (expect 404 - no mutation surface exists)"
done

echo
echo "UC9 complete - interpret results against FINDINGS.md round 2"
