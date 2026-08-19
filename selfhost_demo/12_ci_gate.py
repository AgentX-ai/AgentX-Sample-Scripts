"""
The CI gate: block a merge when eval quality drops.

Two runs of the same golden dataset, gated the way a CI job would gate them: a baseline run with
a healthy "agent" passes the rating floor, then a deliberately regressed run fails both the floor
and the no-regression check against the baseline. The gate is an exit-code contract
(gate.exit_code: 0 = merge, 1 = block) and every gate is recorded into the dashboard's CI Gates
tab (sidebar > Automations), including a caller label so history shows who gated.

In a real pipeline the only difference is that `my_agent` imports YOUR code from the PR's branch
- see the CI Gates tab's setup snippets for the GitHub Actions workflow.
"""

import os
import time

import requests
from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    key = os.getenv("AGENTX_API_KEY")
    if not key:
        raise SystemExit(
            "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup."
        )
    return key


API_KEY = local_api_key()
client = AgentX(api_key=API_KEY, base_url=BASE_URL)


# --- Step 1: a small golden dataset ---------------------------------------------------------
dataset = (
    client.evaluations.datasets.builder(
        name="CI Gate Demo Dataset",
        description="Three support questions with expected answers, for gating demo runs.",
        acceptance_criteria="Accurate, matches the documented policy, gives the customer a concrete answer.",
        rejection_criteria="Vague, wrong, or dodges the question.",
    )
    .add_case(
        query="What's your return window?",
        expected_results="30 days from delivery for most items, full refund.",
    )
    .add_case(
        query="Do you ship internationally?",
        expected_results="Yes, to over 40 countries; shipping cost is calculated at checkout.",
    )
    .add_case(
        query="How do I reset my password?",
        expected_results="Use the 'Forgot password' link on the login page; a reset email arrives within a few minutes.",
    )
    .publish()
)
print(f"Dataset: {dataset.id}")

GOOD_ANSWERS = {
    "return": "You have 30 days from delivery to return most items for a full refund.",
    "ship": "Yes - we ship to over 40 countries, and the exact shipping cost is calculated at checkout.",
    "password": "Click the 'Forgot password' link on the login page and you'll get a reset email within a few minutes.",
}


def healthy_agent(case):
    # Stand-in for your real agent at the CURRENT release: answers match policy.
    for key, answer in GOOD_ANSWERS.items():
        if key in case.query.lower():
            return answer
    return "Let me check that for you."


def regressed_agent(case):
    # Stand-in for the PR that broke the agent: polite, useless, occasionally wrong.
    return "Thanks for your question! Our policies are available somewhere on the website."


# --- Step 2: baseline run passes the gate ---------------------------------------------------
print("\n=== Baseline run (current release) ===")
baseline = (
    client.evaluations.run(dataset_id=dataset.id, subject={"kind": "custom_agent", "framework": "raw_python"})
    .execute(healthy_agent)
    .finalize()
)
gate = baseline.gate(fail_under=7, caller="ci-demo-baseline")
print(f"exit code: {gate.exit_code}  (0 = CI proceeds to merge)")

time.sleep(1)  # keep run ordering unambiguous for the no-regression baseline lookup

# --- Step 3: regressed run fails it ---------------------------------------------------------
print("\n=== Regressed run (the PR under review) ===")
regressed = (
    client.evaluations.run(dataset_id=dataset.id, subject={"kind": "custom_agent", "framework": "raw_python"})
    .execute(regressed_agent)
    .finalize()
)
gate = regressed.gate(fail_under=7, no_regression=True, caller="ci-demo-pr")
print(f"exit code: {gate.exit_code}  (1 = CI blocks the merge)")
print("In a real workflow this line is simply: sys.exit(gate.exit_code)")


# --- Step 4: the gates are on record --------------------------------------------------------
history = requests.get(
    f"{BASE_URL}/evaluate/ci/gates", headers={"x-api-key": API_KEY}, timeout=15
).json()["gates"]
ours = [g for g in history if g["caller"] in ("ci-demo-baseline", "ci-demo-pr")][:4]
print("\nRecorded gate history (dashboard: sidebar > Automations > CI Gates):")
for g in ours:
    checks = "  ".join(f"{'PASS' if c['passed'] else 'FAIL'}:{c['check']}" for c in (g["checks"] or []))
    print(f"  {'PASS' if g['passed'] else 'FAIL'}  avg={g['averageRating']}  caller={g['caller']}  {checks}")
