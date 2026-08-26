"""
04 - Head-to-head judging, and the two pytest assertions that gate a merge.

Absolute averages answer "is it above the bar"; they are bad at "did this change help", because
judge scores drift and cluster. compare_pairwise() asks the judge which of two runs answered
each question better - order-blind, with both_orders judging every pair twice with the sides
swapped so position bias is measured (flip rate) instead of silently deciding the result.

Then the pytest layer: assert_evaluation (a floor) and assert_pairwise (a comparison), which
are the two claims a CI job actually wants to make.

Needs a judge key on the engine. Run:
  AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 04_pairwise_and_pytest.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX
from agentx.testing import assert_evaluation, assert_pairwise, EvaluationAssertionError

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval dive 04 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

dataset = (
    client.evaluations.datasets.builder(name="Support answers")
    .add_case(query="How long do I have to return an item?",
              expected_results="30 days from delivery, unused and in original packaging.")
    .add_case(query="Do I need a receipt?",
              expected_results="Yes - proof of purchase is required; a digital receipt works.")
    .add_case(query="Who pays return shipping?",
              expected_results="We pay for defective items; the customer pays otherwise.")
    .publish()
)

VAGUE = {
    "How long do I have to return an item?": "Returns are generally possible for a while.",
    "Do I need a receipt?": "Maybe - it depends on the circumstances.",
    "Who pays return shipping?": "Shipping is handled according to our policy.",
}
STRONG = {
    "How long do I have to return an item?":
        "You have 30 days from delivery, as long as the item is unused and in its original packaging.",
    "Do I need a receipt?":
        "Yes - we need proof of purchase, and a digital receipt or order confirmation works.",
    "Who pays return shipping?":
        "We cover return shipping for defective items; for other returns the shipping cost is yours.",
}

def run_with(answers):
    return (
        client.evaluations.run(dataset_id=dataset.id, subject={"kind": "custom_agent", "framework": "raw_python"})
        .execute(lambda case: answers[case.query])
        .finalize()
    )

baseline = run_with(VAGUE)
candidate = run_with(STRONG)

# --- 1. assert_evaluation: the floor -----------------------------------------------------------
try:
    assert_evaluation(candidate, min_rating=7.0)
    check("assert_evaluation passes the strong run", True)
except EvaluationAssertionError as e:
    check("assert_evaluation passes the strong run", False, str(e).splitlines()[0])

try:
    assert_evaluation(baseline, min_rating=7.0)
    check("assert_evaluation fails the vague run", False, "it passed")
except EvaluationAssertionError:
    check("assert_evaluation fails the vague run", True)

# --- 2. Head to head: which run answered better, question by question --------------------------
comparison = client.evaluations.compare_pairwise(candidate.run_id, baseline.run_id, both_orders=True)
s = comparison.summary
check("the strong run wins the head-to-head", s.winner == "a", f"{s.a_wins}-{s.b_wins}-{s.ties}")
check("flip rate is measured, not assumed", s.flip_rate is not None, f"flip_rate={s.flip_rate}")
check("verdicts carry per-question reasoning", all(c.justification for c in comparison.cases))
check("presentation order alternates (anti position-bias)",
      len({c.presented_first for c in comparison.cases}) == 2,
      f"{[c.presented_first for c in comparison.cases]}")

# --- 3. assert_pairwise: the merge-gate claim "this change actually helped" --------------------
try:
    assert_pairwise(comparison, must_win=True, max_losses=0, max_flip_rate=0.34)
    check("assert_pairwise passes the improvement", True)
except EvaluationAssertionError as e:
    check("assert_pairwise passes the improvement", False, str(e).splitlines()[0])

backwards = client.evaluations.compare_pairwise(baseline.run_id, candidate.run_id)
try:
    assert_pairwise(backwards, must_win=True)
    check("assert_pairwise fails the regression", False, "it passed")
except EvaluationAssertionError:
    check("assert_pairwise fails the regression", True)

print()
if failures:
    print(f"FAILED: {failures}")
    sys.exit(1)
print("Pairwise + pytest verified: the floor and the comparison, both enforceable in CI.")
