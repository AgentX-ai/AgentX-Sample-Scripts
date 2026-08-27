"""
03 - Eval traffic never pollutes production monitoring, proven.

An offline evaluation produces REAL traces (that is what makes trajectory judging and View
Trace work), but they are not production traffic. The SDK stamps everything created inside
execute() as source="eval-run" automatically - no flag to remember - and the engine keeps that
traffic out of KPIs, live scoring, and signals. This script proves each of those with an
instrumented agent and zero special flags.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 03_eval_traffic_separation.py
Needs an LLM judge key on the engine (a few judge calls: 1 live score + 2 offline gradings).
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval ops 03 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. A live scorer watching ALL traffic, and one real production trace --------------------
scorer = client.monitor.judge_scorers.create(
    "Live quality",
    judge={"evaluationCriteria": "The answer must be helpful and specific."},
    online={"enabled": True, "sampleRate": 1, "alertThreshold": 5, "severity": "medium"},
)

with client.tracer.trace("prod-agent", input="Where is my order #42?", sync=True) as span:
    span.output = "Order #42 shipped yesterday and arrives tomorrow."

live_events = []
for _ in range(30):
    live_events = client.monitor.judge_scorers.events(scorer.id, window="24h")
    if live_events:
        break
    time.sleep(1)
check("production traffic IS live-scored (baseline)", len(live_events) == 1, f"{len(live_events)} event(s)")

kpis_before = client.monitor.kpis()
runs_before = kpis_before.get("totalRuns")
check("production KPIs see exactly the 1 production trace", runs_before == 1, f"totalRuns={runs_before}")

# --- 2. An offline eval whose agent is FULLY instrumented - worst-case pollution -------------
dataset = (
    client.evaluations.datasets.builder(
        name="Order questions",
        evaluation_criteria="The answer must reference the order number and give a delivery status.",
    )
    .add_case(query="Where is order #7?", expected_results="Order #7 status with a date.")
    .add_case(query="Where is order #9?", expected_results="Order #9 status with a date.")
    .publish()
)

def agent(case):
    # A real traced agent call - inside execute(), the SDK stamps this trace source="eval-run"
    # (and monitor=False) via the eval-run scope. No flags here, on purpose.
    with client.tracer.trace("prod-agent", input=case.query, sync=True) as s:
        s.output = f"{case.query.split('order ')[1].rstrip('?')} shipped and arrives Friday."
    return {"output": s.output, "trace_id": s.trace_id}

run = client.evaluations.run(dataset.id, {"displayName": "prod-agent"})
run.execute(agent).finalize()
rows = run.results()
check("the eval created REAL linked traces (View Trace works)", all(r.trace_id for r in rows))
check("the eval rows were judged", all(r.rating is not None for r in rows), [r.rating for r in rows])

# --- 3. Production surfaces are untouched ----------------------------------------------------
time.sleep(2)  # let any (wrongly) triggered background scoring land before we assert it didn't
kpis_after = client.monitor.kpis()
check("production KPIs still see exactly 1 run - eval traces are excluded",
      kpis_after.get("totalRuns") == 1, f"totalRuns={kpis_after.get('totalRuns')}")

events_after = client.monitor.judge_scorers.events(scorer.id, window="24h")
check("the live scorer never judged the eval traffic (no double judging, no double spend)",
      len(events_after) == 1, f"{len(events_after)} event(s), still just the production one")

signals = client.monitor.signals.list(polarity="all")
eval_signals = [s for s in signals if "eval" in (s.summary or "").lower()]
check("no signals were raised by eval traffic", len(eval_signals) == 0, f"{len(signals)} signal(s) total")

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nSeparation verified: a nightly eval cannot skew the production dashboards it feeds.")
