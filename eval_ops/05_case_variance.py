"""
05 - Repetitions surface flakiness per case, instead of flattening it away.

An agent that answers a case correctly twice and wrongly once is a WORSE sign than one that is
consistently mediocre - but a single average hides that. With number_of_requests > 1, every
case runs N times, and the run wire reports per-case min/max/variance (caseStatistics), so
"this case flakes between 2 and 9" is a number, not a hunch.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 05_case_variance.py
Needs an LLM judge key on the engine (3 judge calls: one per repetition).
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval ops 05 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

dataset = (
    client.evaluations.datasets.builder(
        name="Flaky agent",
        number_of_requests=3,
        evaluation_criteria="The stated number of days must match the reference exactly.",
    )
    .add_case(query="How long is the return window?", expected_results="30 days.")
    .publish()
)

# Deterministically flaky: right on repetitions 1 and 3, confidently wrong on repetition 2.
def agent(case):
    if case.run_number == 2:
        return "Our return window is 90 days, no questions asked."
    return "The return window is 30 days."

run = client.evaluations.run(dataset.id, {"displayName": "flaky-bot"})
run.execute(agent).finalize()

rows = run.results()
check("the case ran 3 times (repetitions are real rows)", len(rows) == 3,
      f"{len(rows)} rows, runNumbers={sorted(r.run_number for r in rows)}")
check("every repetition was judged", all(r.rating is not None for r in rows),
      sorted(r.rating for r in rows))

wire = client.evaluations.get_run(run.run_id)
stats = wire.get("caseStatistics", [])
check("caseStatistics reports the case's spread", len(stats) == 1 and stats[0]["ratedCount"] == 3,
      stats)
if stats:
    s = stats[0]
    check("the flake is visible: min far below max",
          s["minRating"] <= 4 and s["maxRating"] >= 7,
          f"min={s['minRating']} max={s['maxRating']}")
    check("variance quantifies it", s["ratingVariance"] > 0, f"variance={s['ratingVariance']}")
    print(f"\n  Case 0 across 3 runs: avg {s['averageRating']}, range {s['minRating']}-{s['maxRating']}, "
          f"variance {s['ratingVariance']} - the flat average alone would have hidden the bad run.")

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nPer-case variance verified: flakiness is measured, not averaged away.")
