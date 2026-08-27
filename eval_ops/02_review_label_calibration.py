"""
02 - The human review loop, scripted end to end: sample -> label -> calibration -> tuning evidence.

The judge's verdicts only mean something if they are checked against people. This script runs
the whole loop from code: a live scorer judges real traffic, a human (this script) sends a trace
to review, labels it with a corrected score, and that label becomes (a) the judge's per-scorer
calibration ground truth and (b) the evidence judge tuning proposes rewrites from.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 02_review_label_calibration.py
Needs an LLM judge key on the engine (2 judge calls).
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval ops 02 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. A live judge scoring all traffic -----------------------------------------------------
scorer = client.monitor.judge_scorers.create(
    "Refund answer quality",
    judge={
        "evaluationCriteria": "The answer must state the store's actual policy concretely. "
        "Polite fluff without policy facts is a failure."
    },
    online={"enabled": True, "sampleRate": 1, "alertThreshold": 5, "severity": "medium"},
)

# The contested case: a polished answer whose policy claim only a human can actually verify
# (the judge has no reference to check the 60-day claim against, so whichever way it rules,
# the human reviewer below is the authority - and overrules it).
with client.tracer.trace(
    "refund-bot",
    input="Can I return a laptop I bought 45 days ago?",
    sync=True,
) as span:
    span.output = (
        "Absolutely! Our generous 60-day return policy covers your laptop - just bring your "
        "receipt to any store for a full refund."
    )
trace_id = span.trace_id

# Live scoring is fire-and-forget after ingest; wait for the verdict to land.
verdict_rating = None
for _ in range(30):
    events = client.monitor.judge_scorers.events(scorer.id, window="24h")
    if events:
        verdict_rating = events[0].rating
        break
    time.sleep(1)
check("the live judge scored the trace", verdict_rating is not None, f"rating={verdict_rating}")

# --- 2. A human sends it to review and OVERRULES the judge -----------------------------------
# Whichever way the judge ruled, the human knows the ground truth it cannot: the real policy.
# Label the opposite verdict, so this exact trace becomes a judge/human disagreement.
item = client.monitor.review_queue.queue(trace_id, note="spot-check: policy claim looks off")
check("the queued item carries the judge's own score (the calibration pair's first half)",
      item.judge_score_at_queue == verdict_rating, f"snapshot={item.judge_score_at_queue}")

judge_said_bad = (verdict_rating or 0) < 5
if judge_said_bad:
    labeled = client.monitor.review_queue.label(
        item.id, "good", corrected_score=9,
        note="False alarm - the 60-day window is real for laptops under the fall promo.",
    )
else:
    labeled = client.monitor.review_queue.label(
        item.id, "bad", corrected_score=2,
        note="Real policy is 30 days - this invents a 60-day one.",
    )
expected_label = "good" if judge_said_bad else "bad"
check("label recorded", labeled.label == expected_label, labeled.label)

# --- 3. The label is now the judge's ground truth --------------------------------------------
calibration = client.monitor.judge_scorers.calibration(scorer.id, window="7d")
check("calibration compared the verdict against the human label", calibration.get("withGroundTruth", 0) >= 1,
      f"withGroundTruth={calibration.get('withGroundTruth')}")
check("the overruled verdict shows up as a disagreement, not silence",
      calibration.get("missed", 0) + calibration.get("overFlagged", 0) >= 1,
      f"missed={calibration.get('missed')} overFlagged={calibration.get('overFlagged')}")
disagreements = calibration.get("disagreementCases", [])
review_sourced = [c for c in disagreements if c.get("groundTruth", {}).get("source") == "review"]
check("the disagreement is sourced from HUMAN REVIEW, with the reviewer's words",
      len(review_sourced) >= 1 and bool(review_sourced[0]["groundTruth"].get("detail")),
      review_sourced[0]["groundTruth"].get("detail") if review_sourced else "none")

# --- 4. And it is exactly what judge tuning would rewrite from -------------------------------
# (tune() spends a judge call generating the rewrite; here we only verify the evidence feed.
# selfhost_demo/11 walks the full tune -> validate -> provenance-gated publish.)
check("tuning has evidence to work from", len(disagreements) >= 1, f"{len(disagreements)} case(s)")

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nReview loop verified: a human label became calibration ground truth and tuning evidence.")
