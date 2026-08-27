"""
04 - When the judge CANNOT score, nothing pretends it did.

The trust property behind every number in the product: a judge failure (missing provider key,
outage, unusable output) is a fact about the JUDGE, never a verdict about the agent. This
script points a scorer at a judge model whose provider has no key configured and proves:

  - offline: rows come back status "skipped" with rating null - excluded from the average and
    the CI gate, and counted separately (skippedCount) instead of dragging the score to 0
  - online: no Signal fires and no rating lands on the charts - a provider outage can never
    page you about "bad agent quality"

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 04_judge_failure_trust.py
Deliberately needs NO Anthropic key on the engine (that missing key IS the failure under test).
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval ops 04 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# A scorer whose judge model routes to Anthropic - which this engine has no key for. Every
# judge call through it will fail the same way a provider outage fails.
scorer = client.monitor.judge_scorers.create(
    "Broken-judge demo",
    judge={"evaluationCriteria": "Anything.", "judgeModel": "claude-sonnet-4-5"},
    # Jaccard on the scorer's OFFLINE profile: when a run grades with a chosen scorer, that
    # scorer's metric toggles apply (not the dataset's own) - and it must keep producing
    # numbers even while the judge half is down.
    offline={"jaccardSimilarity": {"enabled": True}},
    online={"enabled": True, "sampleRate": 1, "alertThreshold": 5, "severity": "high"},
)

# --- 1. Offline: skipped, not zeroed ---------------------------------------------------------
dataset = (
    client.evaluations.datasets.builder(name="Failure semantics")
    .add_case(query="What is the return window?", expected_results="30 days.")
    .publish()
)
run = client.evaluations.run(dataset.id, {"displayName": "any-agent"}, scorer_id=scorer.id)
run.execute(lambda case: "You have 30 days.").finalize()

rows = run.results()
check("the row is SKIPPED with a null rating - not scored 0", rows[0].rating is None)
wire = client.evaluations.get_run(run.run_id)
stats = wire.get("liveStatistics", {})
check("the run average is null, not dragged down by fake zeros", stats.get("averageRating") is None,
      f"avg={stats.get('averageRating')}")
check("skipped rows are counted, visibly", stats.get("skippedCount") == 1,
      f"skippedCount={stats.get('skippedCount')}")
check("per-row status names the skip", wire["results"][0].get("status") == "skipped",
      wire["results"][0].get("status"))
check("the free deterministic metrics still landed (they need no judge)",
      rows[0].jaccard_similarity is not None, f"jaccard={rows[0].jaccard_similarity}")

# --- 2. Online: silent about the agent, honest about the judge -------------------------------
with client.tracer.trace("any-agent", input="hello", sync=True) as span:
    span.output = "hi there"
time.sleep(3)  # give the (failing) fire-and-forget judge time to run

signals = client.monitor.signals.list()
mine = [s for s in signals if "Broken-judge" in (s.summary or "")]
check("NO signal fired from the judge failure (a provider outage never pages as agent quality)",
      len(mine) == 0, f"{len(mine)} signal(s)")
events = client.monitor.judge_scorers.events(scorer.id, window="24h")
check("no fake rating landed on the charts", len(events) == 0, f"{len(events)} rated event(s)")

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nJudge-failure semantics verified: failures are visible as failures, never as scores.")
