"""
01 - The offline evaluation lifecycle, end to end, with the CI gate doing its job.

Dataset -> run -> typed results -> gate. The part worth proving rather than demoing: the gate
FAILS the bad version and PASSES the fixed one against the same dataset, no_regression compares
the two runs, and the deterministic scorers (similarity metrics + a code scorer) produce numbers
on every row independent of any judge - they are what still works, unchanged, on an engine with
no LLM key at all.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 01_offline_lifecycle.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval dive 01 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. A dataset with free scorers: similarity metrics + a code scorer ----------------------
# No judge is configured anywhere in this script, so every number here is deterministic.
dataset = (
    client.evaluations.datasets.builder(
        name="Returns policy",
        jaccard_similarity=True, bleu_score=True, rouge_score=True,
        code_scorers=[{
            "name": "mentions_days",
            "enabled": True,
            "language": "js",
            # A real regression check: the answer must cite the concrete number, not vibes.
            "code": "return { score: /30 days/.test(output) ? 1 : 0, reasoning: '30-day check' };",
        }],
    )
    .add_case(query="How long do I have to return an item?",
              expected_results="30 days from delivery, unused and in original packaging.")
    .add_case(query="Do I need a receipt?",
              expected_results="Yes - a digital receipt or order confirmation works.")
    .publish()
)

BAD = {
    "How long do I have to return an item?": "Returns are possible for a while after purchase.",
    "Do I need a receipt?": "Maybe. It depends on the store.",
}
GOOD = {
    "How long do I have to return an item?": "You have 30 days from delivery; items must be unused and in original packaging.",
    "Do I need a receipt?": "Yes - a digital receipt or your order confirmation email works.",
}

def run_version(answers, version):
    return (
        client.evaluations.run(
            dataset_id=dataset.id,
            subject={"kind": "custom_agent", "framework": "raw_python", "metadata": {"version": version}},
        )
        .execute(lambda case: answers[case.query])
        .finalize()
    )

# --- 2. v1 is bad, and both kinds of scorer say so --------------------------------------------
v1 = run_version(BAD, "v1")
rows = v1.results()
sims = [r.jaccard_similarity for r in rows]
check("similarity metrics computed on every row", all(s is not None for s in sims),
      f"jaccard={[round(s, 2) for s in sims]}")
code_rows = [cr for r in rows for cr in (r.code_scorer_results or []) if cr.get("name") == "mentions_days"]
check("code scorer ran per row", len(code_rows) == len(rows),
      f"scores={[cr.get('score') for cr in code_rows]}")
check("code scorer caught the missing number", all(cr.get("score") == 0 for cr in code_rows))

# The gate is the CI verdict: the bad version must fail the floor.
gate1 = v1.gate(fail_under=7, caller="eval-dive")
check("gate fails the bad version", not gate1.passed, f"avg={gate1.average_rating}")

# --- 3. v2 is fixed; per-case comparison shows where -----------------------------------------
v2 = run_version(GOOD, "v2")
rows2 = v2.results()
code_rows2 = [cr for r in rows2 for cr in (r.code_scorer_results or []) if cr.get("name") == "mentions_days"]
check("fixed version passes the code scorer where it should",
      any(cr.get("score") == 1 for cr in code_rows2),
      f"scores={[cr.get('score') for cr in code_rows2]}")
by_q1 = {r.question_text: r.jaccard_similarity or 0 for r in rows}
by_q2 = {r.question_text: r.jaccard_similarity or 0 for r in rows2}
better = [by_q1[q] < by_q2[q] for q in by_q1]
check("every case's similarity improved v1 -> v2", all(better),
      f"v1={[round(v, 2) for v in by_q1.values()]} v2={[round(v, 2) for v in by_q2.values()]}")

# The fixed version clears the same floor AND did not regress against the previous run.
gate2 = v2.gate(fail_under=7, no_regression=True, caller="eval-dive")
check("gate passes the fixed version incl. no-regression vs v1", gate2.passed,
      f"avg={gate2.average_rating} baseline={gate2.baseline_average}")

# --- 4. The gate history is a record, not a printout ------------------------------------------
gates = client.evaluations.list_gates()
mine = [g for g in gates if g.get("caller") == "eval-dive"]
check("recorded gates appear in CI history with their caller", len(mine) >= 2, f"found {len(mine)}")

print()
if failures:
    print(f"FAILED: {failures}")
    sys.exit(1)
print("Offline lifecycle: dataset, run, typed rows, deterministic scorers, gate history - all verified.")
