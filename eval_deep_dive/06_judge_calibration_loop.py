"""
06 - Is the judge itself right? Ground truth in, calibration out.

Every score in scripts 02-05 was an LLM's opinion. This script closes the loop that keeps those
opinions honest: real-world outcomes (a reopened ticket, a confirmed resolution) and end-user
votes are reported AGAINST traces the judge already scored, and calibration turns the
agreement/disagreement into rates - the number that tells you whether to trust the judge, per
window, before you tune anything.

The check that matters: a judge verdict contradicted by reality shows up as disagreement, not
as silence.

Needs a judge key on the engine. Run:
  AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 06_judge_calibration_loop.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval dive 06 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# A live scorer, judging everything.
scorer = client.monitor.judge_scorers.builder(
    "Resolution quality",
    acceptance_criteria="The reply resolves the customer's problem or gives a concrete next step.",
    rejection_criteria="The reply deflects, hedges, or leaves the problem unresolved.",
    live=True, sample_rate=1.0, alert_threshold=6,
).publish()

def serve(q, a):
    with client.tracer.trace("calib-agent", input={"q": q}, sync=True) as span:
        span.output = a
    return span.trace_id

# Three traces: one genuinely good, one genuinely bad, and the interesting one - a reply that
# READS well (the judge will like it) but did not survive contact with reality.
t_good = serve("My order arrived damaged.",
               "I'm sorry - I've issued a replacement shipping today and emailed you the tracking number.")
t_bad = serve("My order arrived damaged.",
              "That's unfortunate. Many customers have this experience.")
t_looks_good = serve("I was double-charged.",
                     "I've fully resolved this: the duplicate charge is reversed and will post within 1-2 days.")

deadline = time.time() + 90
while time.time() < deadline:
    if len(client.monitor.judge_scorers.events(scorer.id, window="24h")) >= 3:
        break
    time.sleep(3)
events = {e.trace_id: e for e in client.monitor.judge_scorers.events(scorer.id, window="24h")}
check("all three traces judged", len(events) >= 3, f"{len(events)}")
check("the polished reply scored well (the judge believed it)",
      t_looks_good in events and events[t_looks_good].rating >= 6,
      f"rating={events.get(t_looks_good) and events[t_looks_good].rating}")

# --- Ground truth arrives, from systems that know what actually happened ----------------------
# The good one is confirmed good; the polished one turns out to have been wrong: the customer
# was charged again next cycle. is_negative is the polarity calibration compares against.
client.outcomes.report(trace_id=t_good, outcome="confirmed_resolved", is_negative=False)
client.outcomes.report(trace_id=t_looks_good, outcome="charge_reoccurred", is_negative=True,
                       reason="Duplicate charge came back on the next statement.")
# And the user who got the bad reply downvotes it - the second ground-truth stream.
client.feedback.report(t_bad, "down", comment="Did not help at all.")

time.sleep(3)
cal = client.monitor.calibration(window="24h")
check("calibration compared judge verdicts against ground truth", (cal.get("comparedCount") or 0) >= 2,
      f"comparedCount={cal.get('comparedCount')}")
# The polished-but-wrong reply is a judge miss: reality said negative, the judge said good.
# That must surface as a false negative, not vanish into an average.
check("the judge's miss shows up as disagreement, not silence",
      (cal.get("agreementRate") or 1.0) < 1.0 and (cal.get("falseNegativeRate") or 0) > 0,
      f"agreementRate={round(cal.get('agreementRate', 0), 2)} falseNegativeRate={cal.get('falseNegativeRate')}")

# Per-scorer view of the same truth, addressed via the scorer's online profile.
per = client.monitor.judge_scorers.calibration(scorer.id, window="24h")
check("per-scorer calibration exists for tuning to read", isinstance(per, dict) and per,
      f"keys={sorted(per.keys())[:5]}")

print()
if failures:
    print(f"FAILED: {failures}")
    sys.exit(1)
print("Calibration loop verified: reality reported in, judge agreement measured, misses visible.")
