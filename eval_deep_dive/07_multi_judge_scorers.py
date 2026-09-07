"""
07 - Multiple judge scorers on one dataset run, and the named-scorer CI gate.

One agent execution, N verdicts (self-host): `additional_scorer_ids` makes every extra judge
scorer grade each result by ITS OWN rubric and judge model, without re-running the agent. The
part worth proving: the primary keeps the `rating` column (averages and the default gate are
unchanged), each extra verdict lands labeled on the result row (`judge_scorer_results`) and in
the run's per-scorer aggregate (`scorerBreakdown`), and `gate(scorer="Safety")` fails the run
on the Safety scorer's own average even while the primary's gate passes - "fail if Safety is
low even when the average looks fine". An unknown scorer name is a hard error, never a
silently-passing gate on nothing.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 07_multi_judge_scorers.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX
from agentx.evaluations.client import AgentXEvaluationsError

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval dive 07 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. Three judge scorers, three rubrics ----------------------------------------------------
# Deliberately polarized criteria so the verdicts are predictable: Quality rewards any relevant
# answer, Safety demands a literal marker no answer will contain. That split is the whole
# point - one response, judged differently by different rubrics.
quality = client.monitor.judge_scorers.builder(
    "Quality",
    acceptance_criteria="Score 9-10 for any answer that addresses the question in plain language.",
    rejection_criteria="Only reject answers that are empty or completely off-topic.",
).publish()
safety = client.monitor.judge_scorers.builder(
    "Safety",
    acceptance_criteria="Score 9-10 ONLY if the answer contains the literal token SAFETY-REVIEWED.",
    rejection_criteria="Score 0-2 if the literal token SAFETY-REVIEWED is missing from the answer.",
).publish()
tone = client.monitor.judge_scorers.builder(
    "Tone",
    acceptance_criteria="Professional, calm wording.",
    rejection_criteria="Sarcasm, slang, or hostility.",
).publish()
print(f"  scorers: quality={quality.id} safety={safety.id} tone={tone.id}")

dataset = (
    client.evaluations.datasets.builder(name="Multi-judge policy answers")
    .add_case(query="How long is the return window?",
              expected_results="30 days from delivery.")
    .add_case(query="Do you ship internationally?",
              expected_results="Yes, to most countries, in 7-14 business days.")
    .publish()
)

ANSWERS = {
    "How long is the return window?": "You have 30 days from delivery to return an item.",
    "Do you ship internationally?": "Yes - we ship to most countries; delivery takes 7-14 business days.",
}

# --- 2. One run, one execution per case, three verdicts per result ----------------------------
# The primary is passed the modern way (scorer_id); the repeated primary in the additional list
# must be deduped by the engine, not double-scored.
run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "displayName": "multi-judge-demo"},
        scorer_id=quality.id,
        additional_scorer_ids=[safety.id, tone.id, quality.id],
    )
    .execute(lambda case: ANSWERS[case.query])
    .finalize()
)

rows = run.results()
check("every result carries one labeled verdict per ADDITIONAL scorer",
      all(len(r.judge_scorer_results or []) == 2 for r in rows),
      f"{[len(r.judge_scorer_results or []) for r in rows]} extra verdicts per row")

by_name = {}
for r in rows:
    for v in r.judge_scorer_results or []:
        by_name.setdefault(v["name"], []).append(v["rating"])
check("verdicts are labeled by scorer name", set(by_name) == {"Safety", "Tone"}, str(set(by_name)))

primary_ratings = [r.rating for r in rows]
check("primary verdict keeps the rating column and scores high",
      all(x is not None and x >= 6 for x in primary_ratings), f"ratings={primary_ratings}")
safety_ratings = [x for x in by_name.get("Safety", []) if x is not None]
check("Safety's own rubric scores the SAME answers low",
      len(safety_ratings) == len(rows) and all(x <= 4 for x in safety_ratings),
      f"ratings={safety_ratings}")

# --- 3. The run's per-scorer aggregate, primary first -----------------------------------------
detail = client.evaluations.get_run(run.run_id)
breakdown = detail.get("scorerBreakdown") or []
check("scorerBreakdown aggregates all three scorers, primary first",
      [b.get("name") for b in breakdown] == ["Quality", "Safety", "Tone"]
      and breakdown[0].get("primary") is True,
      str([(b.get("name"), b.get("averageRating")) for b in breakdown]))
check("the repeated primary was deduped from additionalScorerIds",
      detail.get("additionalScorerIds") == [safety.id, tone.id],
      str(detail.get("additionalScorerIds")))

# --- 4. Gates: primary passes, the NAMED Safety scorer fails the same run ---------------------
primary_gate = run.gate(fail_under=5)
check("primary gate passes at the same floor", primary_gate.passed and primary_gate.gated_scorer is None)

safety_gate = run.gate(fail_under=5, scorer="Safety")
check("gate(scorer='Safety') fails the SAME run on Safety's own average",
      not safety_gate.passed and (safety_gate.gated_scorer or {}).get("name") == "Safety",
      f"safety average={safety_gate.average_rating}")

try:
    run.gate(fail_under=5, scorer="NoSuchScorer")
    check("unknown scorer name is a hard error, never a silent pass", False)
except AgentXEvaluationsError as exc:
    check("unknown scorer name is a hard error, never a silent pass", True, str(exc)[:80])

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("multi-judge scorers: all green")
