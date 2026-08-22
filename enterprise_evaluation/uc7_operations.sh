#!/usr/bin/env bash
# UC7 - Operations surface probes. Deliberately curl-based: this tests the ops/REST surface
# itself. Assumes the UC engine on :4791 plus permission to boot a second one on :4792.
#
#   AGENTX_ENGINE_DIR=.../AgentX-trace-eval/engine bash uc7_operations.sh
set -uo pipefail
BASE=${AGENTX_SELFHOST_BASE_URL:-http://localhost:4791/api/v1}

echo "=== 7.1 project isolation ==="
A=$(curl -s -X POST "$BASE/projects" -H 'content-type: application/json' -d '{"name":"iso-A"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['project']['apiKey'])")
B=$(curl -s -X POST "$BASE/projects" -H 'content-type: application/json' -d '{"name":"iso-B"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['project']['apiKey'])")
curl -s -X POST "$BASE/ingest/traces" -H "x-api-key: $A" -H 'content-type: application/json' \
  -d '{"name":"iso-agent","input":"secret question","output":"secret answer","span_id":"iso-1"}' > /dev/null
COUNT_B=$(curl -s "$BASE/ingest/traces?limit=50" -H "x-api-key: $B" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('traces',[])))")
echo "project B sees $COUNT_B of project A's traces (expect 0, minus seeds: fresh project has 3 seed traces)"
CROSS=$(curl -s "$BASE/ingest/traces?limit=50" -H "x-api-key: $B" | grep -c "secret answer" || true)
echo "cross-project leakage of A's content into B: $CROSS (expect 0)"

echo
echo "=== 7.2 no compliance placebos on the settings surface ==="
# History: this probe originally caught redactionMode as a no-op (accepted, stored, never
# applied). The knob has since been REMOVED from the monitoring-defaults surface - the honest
# resolution. The probe now guards against its return: the settings wire must not advertise a
# redaction field, and sending one must not round-trip.
curl -s -X PUT "$BASE/agent-monitoring/settings/monitoring-defaults" -H "x-api-key: $A" \
  -H 'content-type: application/json' -d '{"redactionMode":"strict"}' > /dev/null
PLACEBO=$(curl -s "$BASE/agent-monitoring/settings" -H "x-api-key: $A" | grep -c "redactionMode" || true)
echo "redaction fields advertised by monitoring defaults: $PLACEBO (expect 0 - no placebo knobs)"

echo
echo "=== 7.3 ingest throughput (100 sequential root traces) ==="
python3 - "$BASE" "$A" <<'PY'
import json, sys, time, urllib.request
base, key = sys.argv[1], sys.argv[2]
lat = []
for i in range(100):
    body = json.dumps({"name":"bench-agent","input":f"q{i}","output":"a","span_id":f"bench-{i}"}).encode()
    req = urllib.request.Request(f"{base}/ingest/traces", data=body, method="POST",
                                 headers={"x-api-key": key, "Content-Type": "application/json"})
    t0 = time.time()
    urllib.request.urlopen(req).read()
    lat.append((time.time()-t0)*1000)
lat.sort()
print(f"sequential ingest: p50={lat[50]:.1f}ms p95={lat[95]:.1f}ms max={lat[-1]:.1f}ms "
      f"(~{1000/lat[50]:.0f} req/s single-writer)")
PY

echo
echo "=== 7.4 auth-enabled mode (second engine on :4792) ==="
if [ -n "${AGENTX_ENGINE_DIR:-}" ]; then
  TMPHOME=$(mktemp -d)
  (cd "$AGENTX_ENGINE_DIR" && PORT=4792 AGENTX_HOME="$TMPHOME" AGENTX_AUTH=enabled npx tsx src/index.ts > "$TMPHOME/log" 2>&1 &)
  for i in $(seq 1 40); do curl -s -o /dev/null http://localhost:4792/api/v1/auth/config && break; sleep 1; done
  CFG=$(curl -s http://localhost:4792/api/v1/auth/config)
  echo "auth config: $CFG"
  ANON=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:4792/api/v1/projects)
  echo "anonymous /projects with auth enabled: HTTP $ANON (expect 401)"
  HANDOUT=$(echo "$CFG" | grep -c apiKey || true)
  echo "key handout in enabled mode: $HANDOUT (expect 0 - no anonymous credentials)"
  pkill -f "PORT=4792" 2>/dev/null; pkill -f "$TMPHOME" 2>/dev/null
else
  echo "skipped (set AGENTX_ENGINE_DIR to run)"
fi

echo
echo "=== 7.5 credential rate limit (default 120/min, AGENTX_RATE_LIMIT_CREDENTIAL tunable) ==="
CODES=$(for i in $(seq 1 130); do curl -s -o /dev/null -w "%{http_code} " "$BASE/auth/config"; done)
LIMITED=$(echo "$CODES" | tr ' ' '\n' | grep -c 429 || true)
echo "429s within 130 rapid credential-route calls: $LIMITED (expect >0 once the 120/min window fills)"

echo
echo "UC7 complete - interpret results against FINDINGS.md"
