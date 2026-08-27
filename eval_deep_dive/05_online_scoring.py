"""
05 - Online evaluation: one scorer, live traffic, verdicts with cost controls.

The offline scripts graded runs you started. Online is the other direction: an LLM Judge
Scorer watches traffic as it arrives - every judge call is real spend, so the scorer owns its
own sampling, and a score below its alert threshold becomes a Signal for the Review queue
rather than a number nobody sees.

What is checked: the same scorer id used offline scores live traces, verdicts land as events
with reasoning, the bad trace (and only the bad trace) raises a signal, and pausing the online
profile stops spend without touching the rubric.

Needs a judge key on the engine. Run:
  AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 05_online_scoring.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval dive 05 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# One scorer: rubric + online profile in a single call. sample_rate=1 here because the test
# needs determinism; in production this is the cost dial, per scorer.
scorer = client.monitor.judge_scorers.builder(
    "Answer concreteness",
    acceptance_criteria="States the concrete policy: numbers, timeframes, conditions.",
    rejection_criteria="Vague, hedging, or refers the user elsewhere without answering.",
    live=True, sample_rate=1.0, alert_threshold=6, severity="medium",
).publish()
check("scorer created with an online profile", scorer.online_profile_id is not None)

def serve(q, a):
    with client.tracer.trace("live-agent", input={"q": q}, sync=True) as span:
        span.output = a
    return span.trace_id

good = serve("How long do I have to return an item?",
             "You have 30 days from delivery; items must be unused and in original packaging.")
bad = serve("How long do I have to return an item?",
            "Our policy is quite generous - please consult the website for details.")

# Verdicts are written asynchronously after ingest; poll briefly.
deadline = time.time() + 60
events = []
while time.time() < deadline:
    events = client.monitor.judge_scorers.events(scorer.id, window="24h")
    if len(events) >= 2:
        break
    time.sleep(3)

check("both live traces were judged", len(events) >= 2, f"{len(events)} events")
by_trace = {e.trace_id: e for e in events}
g, b = by_trace.get(good), by_trace.get(bad)
check("concrete answer scores at/above threshold", g is not None and g.rating >= 6,
      f"rating={g and g.rating}")
check("vague answer scores below threshold", b is not None and b.rating < 6,
      f"rating={b and b.rating}")
check("every verdict carries reasoning", all(e.justification for e in events))

# The below-threshold verdict must page someone: a Signal, tied to the trace.
signals = client.monitor.list_signals(polarity="all")
sig = [s for s in signals if "online-eval" in (getattr(s, "pattern_key", "") or "")]
check("the bad verdict raised exactly one signal", len(sig) == 1, f"{len(sig)} signal(s)")

# Pause online scoring: sparse update, rubric untouched, no further spend.
client.monitor.judge_scorers.update(scorer.id, online={"enabled": False})
serve("Ping?", "Pong - checking that a paused scorer stays quiet.")
time.sleep(8)
after = client.monitor.judge_scorers.events(scorer.id, window="24h")
check("paused scorer judges nothing new", len(after) == len(events), f"{len(events)} -> {len(after)}")
refetched = client.monitor.judge_scorers.get(scorer.id)
check("pausing did not touch the rubric",
      refetched.judge["acceptanceCriteria"].startswith("States the concrete policy"))

print()
if failures:
    print(f"FAILED: {failures}")
    sys.exit(1)
print("Online scoring verified: live verdicts, threshold signals, and a pause that stops spend.")
