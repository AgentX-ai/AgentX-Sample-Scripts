"""
02 - Reference-guided vs reference-free grading, and the analysis report.

One judge rubric, two modes, chosen by the data rather than by configuration:

  - a case WITH expected_results is graded against it as ground truth (reference-guided)
  - a case WITHOUT one is graded on the criteria alone (reference-free) - the default judge
    prompt swaps to its reference-free sibling instead of anchoring on an "N/A" benchmark

The check that matters: a confidently WRONG answer must be punished in reference-guided mode
(it contradicts the ground truth) even though it reads fluent and helpful on its own. Then
.analyze() turns the finished run into a written report with statistics.

Needs a judge key on the engine. Run:
  AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 02_grading_modes_and_analysis.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval dive 02 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. Mixed dataset: two referenced cases, one reference-free ------------------------------
dataset = (
    client.evaluations.datasets.builder(name="Refund policy - mixed modes")
    .add_case(query="What is the refund window?",
              expected_results="30 days from delivery. After 30 days, store credit only.")
    .add_case(query="Can I return opened software?",
              expected_results="No - opened software is non-returnable, per the license terms.")
    # No expected_results: judged reference-free on the criteria alone.
    .add_case(query="Write a one-line apology for a late delivery.")
    .publish()
)

# The answers: correct, CONFIDENTLY WRONG (fluent, specific, contradicts ground truth), fine.
ANSWERS = {
    "What is the refund window?":
        "You have 30 days from delivery for a refund; after that we can only offer store credit.",
    "Can I return opened software?":
        "Absolutely - opened software can be returned within 90 days for a full refund, no questions asked.",
    "Write a one-line apology for a late delivery.":
        "We're sorry your order arrived late - that's on us, and we appreciate your patience.",
}

run = (
    client.evaluations.run(dataset_id=dataset.id, subject={"kind": "custom_agent", "framework": "raw_python"})
    .execute(lambda case: ANSWERS[case.query])
    .finalize()
)
rows = {r.question_text: r for r in run.results()}

correct = rows["What is the refund window?"]
wrong = rows["Can I return opened software?"]
free = rows["Write a one-line apology for a late delivery."]

check("correct answer scores high against its reference", (correct.rating or 0) >= 7,
      f"rating={correct.rating}")
check("confidently wrong answer is punished by the reference",
      wrong.rating is not None and wrong.rating <= 4,
      f"rating={wrong.rating}")
check("reference-free case is judged, not skipped", free.rating is not None,
      f"rating={free.rating}")
check("reference-free case is judged on criteria (a good apology passes)", (free.rating or 0) >= 6,
      f"rating={free.rating}")
check("every verdict comes with reasoning", all(r.justification for r in rows.values()))

# The wrong answer's justification should point at the contradiction, which is what makes a
# judge verdict actionable instead of a bare number.
just = (wrong.justification or "").lower()
check("the wrong answer's justification cites the policy conflict",
      any(w in just for w in ("non-returnable", "cannot", "contradict", "policy", "license")),
      f"'{(wrong.justification or '')[:90]}...'")

# --- 2. The analysis report: from per-row scores to a written verdict ------------------------
report = run.analyze()
stats = report.statistics
check("report carries run statistics", stats is not None and stats.average_rating is not None,
      f"avg={stats.average_rating if stats else None} min={stats.min_rating if stats else None}")
# The report is a written analysis, not just aggregates: the wrong answer must surface in the
# low-scoring cases the report points a human at.
check("low-scoring cases name the failure", len(report.low_scoring_cases) >= 1,
      f"{len(report.low_scoring_cases)} case(s) flagged")

print()
if failures:
    print(f"FAILED: {failures}")
    sys.exit(1)
print("Grading modes + analysis verified: the reference is ground truth when present, criteria when not.")
