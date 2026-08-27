"""
01 - Splits, concurrency, output reuse, and resume: the eval cost controls, verified.

The workflow every CI budget wants: tag a few cases as the "smoke" split and run just those on
PRs; run the full set nightly; when only the JUDGE changed, replay the previous run's recorded
outputs instead of re-running (and re-paying for) the agent; and if a run is interrupted, a
re-execute() resumes past everything already submitted instead of starting over.

Every claim is checked (OK/BAD per line, non-zero exit on failure).

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 01_splits_reuse_resume.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval ops 01 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. A dataset with a "smoke" split -------------------------------------------------------
dataset = (
    client.evaluations.datasets.builder(
        name="Support policy",
        evaluation_criteria="The answer must state the concrete number (days/hours) the policy defines.",
    )
    .add_case(query="How long is the return window?", expected_results="30 days.", splits=["smoke"])
    .add_case(query="How fast do you answer support tickets?", expected_results="Within 24 hours.")
    .add_case(query="How long is the warranty?", expected_results="2 years.", splits=["smoke"])
    .publish()
)

ANSWERS = {
    "How long is the return window?": "You have 30 days from delivery.",
    "How fast do you answer support tickets?": "We reply within 24 hours.",
    "How long is the warranty?": "Two years, parts and labor.",
}
agent_calls = []
def agent(case):
    agent_calls.append(case.query)
    return ANSWERS[case.query]

# --- 2. The cheap PR run: split="smoke", concurrent ------------------------------------------
smoke_run = client.evaluations.run(dataset.id, {"displayName": "policy-bot"}, split="smoke")
smoke_run.execute(agent, concurrency=2).finalize()

check("split run executed only the 2 tagged cases", agent_calls.count("How fast do you answer support tickets?") == 0
      and len(agent_calls) == 2, agent_calls)
rows = smoke_run.results()
check("split rows keep their ORIGINAL case texts (indexes line up with full runs)",
      sorted(r.question_text for r in rows) == ["How long is the return window?", "How long is the warranty?"])
check("split rows were judged", all(r.rating is not None and r.rating >= 6 for r in rows),
      [r.rating for r in rows])
run_wire = client.evaluations.get_run(smoke_run.run_id)
check("the run records which split it covered", run_wire.get("evaluationSubject", {}).get("split") == "smoke")

# --- 3. Resume: re-executing the SAME run re-pays for nothing --------------------------------
calls_before = len(agent_calls)
smoke_run.execute(agent, concurrency=2)
check("re-execute skipped every already-submitted case (resume)", len(agent_calls) == calls_before,
      f"agent calls stayed at {calls_before}")

# --- 4. The nightly full run, reusing the smoke run's outputs --------------------------------
# Only the 1 case the smoke run never touched costs an agent call; the judge still re-scores
# every case with this run's grading config - which is what makes judge iteration cheap.
agent_calls.clear()
full_run = client.evaluations.run(dataset.id, {"displayName": "policy-bot"})
full_run.execute(agent, reuse_outputs_from=smoke_run.run_id).finalize()

check("reuse ran the agent only for the uncached case", agent_calls == ["How fast do you answer support tickets?"],
      agent_calls)
full_rows = full_run.results()
check("all 3 cases scored in the full run", len(full_rows) == 3 and all(r.rating is not None for r in full_rows))
# The reused rows carry the EXACT recorded outputs from the source run (the reuse marker rides
# the submission metadata; what is verifiable on the read side is the replayed output itself).
smoke_outputs = {r.question_text: r.response for r in rows}
replayed = [r for r in full_rows if r.question_text in smoke_outputs and r.response == smoke_outputs[r.question_text]]
check("reused rows replay the source run's recorded outputs", len(replayed) == 2, f"{len(replayed)} replayed")

# --- 5. The gate works the same on both ------------------------------------------------------
gate = full_run.gate(fail_under=5)
check("CI gate passes the full run", gate.passed is True)

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nSplits, concurrency, reuse, and resume verified: PR runs are cheap, judge iteration is free.")
