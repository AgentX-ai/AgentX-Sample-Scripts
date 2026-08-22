"""
UC3 - Online evaluation: scorers on live production traffic.

Buyer questions:
  - Opt-in posture: does a fresh project really judge nothing until told to? (Cost safety.)
  - Do the zero-LLM template scorers catch what they claim (secrets, PII)?
  - Can I deploy MY OWN logic as a code scorer (no endpoint to host) and have it raise
    triage signals with metadata?
  - Do signals dedupe (one row per issue, occurrence-counted), and do KPIs move?
"""

import os
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4791/api/v1")

import requests  # noqa: E402  (FINDING: projects, template-scorer enable + code-scorer CRUD have no SDK surface)

# Idempotent by construction: every run gets its own project (fresh scorer config, fresh KPIs).
boot = requests.post(f"{BASE_URL}/projects", json={"name": f"UC3 online {int(time.time())}"},
                     headers={"Content-Type": "application/json"}, timeout=15)
boot.raise_for_status()
PROJECT_KEY = boot.json()["project"]["apiKey"]
HEADERS = {"x-api-key": PROJECT_KEY, "Content-Type": "application/json"}

client = AgentX(api_key=PROJECT_KEY, base_url=BASE_URL)
client.ping()


def api(method, path, **kwargs):
    r = requests.request(method, f"{BASE_URL}{path}", headers=HEADERS, timeout=15, **kwargs)
    r.raise_for_status()
    return r.json() if r.text else {}


# --- 1. Opt-in proof: trippy traffic with everything off -------------------------------------
with client.tracer.trace("prod-agent", input={"q": "opt-in probe"}, sync=True) as span:
    span.output = "Here is my key sk-proj-Abc123def456ghi789jkl012 and email ada@example.com"
time.sleep(1.5)
before = client.monitor.signals.list(limit=100)
print(f"signals with zero scorers enabled: {len([s for s in before if s.pattern_key != 'healthy-response'])} (expect 0)")

# --- 2. Enable two template scorers + create a code scorer -----------------------------------
api("PUT", "/agent-monitoring/settings/monitoring-defaults",
    json={"enabledBuiltinPatterns": ["secrets-in-response", "pii-in-response"]})

scorer = api("POST", "/agent-monitoring/custom-evaluators", json={
    "name": "Apology overload",
    "kind": "code",
    "language": "python",
    "sampleRate": 1,
    "alertBelow": 0.5,
    "script": (
        "async def handler(input, output, expected, metadata, trace):\n"
        "    text = str(output).lower()\n"
        "    apologies = sum(text.count(w) for w in ('sorry', 'apolog'))\n"
        "    return {'name': 'apology overload', 'score': 0.0 if apologies >= 2 else 1.0,\n"
        "            'metadata': {'apologies': apologies}}\n"
    ),
})["evaluator"]
print(f"code scorer created: {scorer['_id']} kind={scorer['kind']}")

# --- 3. Production-like traffic: mostly clean, a few violations ------------------------------
CLEAN = [
    "Your order ships Thursday - tracking number on its way.",
    "Resetting your password takes about a minute from Settings.",
    "The invoice regenerates once VAT details are saved.",
]
for i, text in enumerate(CLEAN * 3):
    with client.tracer.trace("prod-agent", input={"q": f"clean-{i}"}, sync=True) as span:
        span.output = text

for i in range(3):  # a recurring secrets leak - should dedupe to ONE signal, occurrenceCount 3
    with client.tracer.trace("prod-agent", input={"q": f"leak-{i}"}, sync=True) as span:
        span.output = f"Debug info #{i}: the service key is sk-proj-Abc123def456ghi789jkl012"

with client.tracer.trace("prod-agent", input={"q": "apologetic"}, sync=True) as span:
    span.output = "I'm so sorry! I apologize deeply for the inconvenience, truly sorry again."

time.sleep(3)

# --- 4. Verify: signals, dedupe, KPI movement ------------------------------------------------
signals = client.monitor.signals.list(limit=100)
by_key = {}
for s in signals:
    by_key.setdefault(s.pattern_key, []).append(s)

secrets = by_key.get("secrets-in-response", [])
code_hits = [s for key, rows in by_key.items() if key.startswith("custom-eval:") for s in rows]
print(f"secrets signals: {len(secrets)} row(s), occurrences={secrets[0].occurrence_count if secrets else 0} (expect 1 row x3)")
print(f"code scorer signals: {len(code_hits)} - {code_hits[0].summary[:70] if code_hits else 'none'}")

kpis = client.monitor.kpis(window="24h")
print(f"KPIs: totalRuns={kpis['totalRuns']} failureRate={kpis['failureRate']:.2f} "
      f"scorerFailing={kpis['breakdown']['scorerFailingRuns']}")

events = api("GET", f"/agent-monitoring/custom-evaluators/{scorer['_id']}/events?window=24h")["events"]
scored = [e for e in events if e.get("score") is not None]
print(f"code scorer event history: {len(scored)} scored checks (metadata retained: "
      f"{'apologies' in str(events)})")

# Metric semantics (verified, logged in FINDINGS): pattern detections classify the RUN (3 leak
# traces -> scorerFailingRuns 3), but code/external scorer verdicts are evaluator events - they
# raise signals and keep their own history, without reclassifying the run outcome. Same design
# as LLM-judge scores.
ok = (
    len([s for s in before if s.pattern_key != "healthy-response"]) == 0
    and len(secrets) == 1 and secrets[0].occurrence_count == 3
    and len(code_hits) >= 1
    and kpis["breakdown"]["scorerFailingRuns"] == 3
)
print("\nUC3 PASS" if ok else "\nUC3 FAIL")
