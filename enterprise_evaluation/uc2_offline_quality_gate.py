"""
UC2 - Offline evaluation as a pre-deployment quality gate.

Buyer questions:
  - Can I express a golden dataset with reference answers + judge criteria + cheap deterministic
    metrics in code, versioned, and gate a CI pipeline on it?
  - Does the gate actually fail a regressed build and pass a healthy one (exit-code semantics)?
  - How repeatable are judge scores run-to-run (determinism / variance KPI)?
  - What does one full gated run cost in wall-clock time?

Requires the ENGINE to have an OpenAI key (judge calls are engine-side).
"""

import os
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4791/api/v1")
client = AgentX(api_key=os.environ["AGENTX_API_KEY"], base_url=BASE_URL)
client.ping()

# --- Golden dataset: judge criteria + Jaccard against references ------------------------------
# FINDING: similarity metrics are first-class builder kwargs, but offline CODE scorers are not
# exposed in the SDK dataset builder (dashboard/REST only) - logged in FINDINGS.md.
dataset = (
    client.evaluations.datasets.builder(
        name="Buyer Gate - Support Policy v1",
        description="Golden support questions; regression gate for weekly releases.",
        acceptance_criteria="Accurate per policy, concrete, actionable; no invented policy.",
        rejection_criteria="Vague, dodges the question, or contradicts documented policy.",
        jaccard_similarity=True,
    )
    .add_case(query="What's your return window?",
              expected_results="30 days from delivery for most items, full refund.")
    .add_case(query="Do you ship internationally?",
              expected_results="Yes, to over 40 countries; cost calculated at checkout.")
    .add_case(query="How do I cancel my subscription?",
              expected_results="Settings > Billing > Cancel subscription; effective at period end.")
    .add_case(query="Can I get an invoice with my company VAT number?",
              expected_results="Yes - add the VAT number under Billing details; invoices regenerate.")
    .publish()
)
print(f"dataset: {dataset.id}")

GOOD = {
    "return": "You have 30 days from delivery to return most items for a full refund.",
    "internation": "Yes - we ship to over 40 countries, with cost calculated at checkout.",
    "cancel": "Go to Settings > Billing > Cancel subscription; it takes effect at the period end.",
    "vat": "Yes - add your VAT number under Billing details and invoices will regenerate.",
}


def healthy_agent(case):
    for key, answer in GOOD.items():
        if key in case.query.lower():
            return answer
    return "Let me check that for you."


def regressed_agent(case):
    return "Great question! We really value you as a customer. Please explore our website."


def gated_run(agent, caller):
    t0 = time.time()
    run = (
        client.evaluations.run(dataset_id=dataset.id, subject={"kind": "custom_agent", "framework": "raw_python"})
        .execute(agent)
        .finalize()
    )
    gate = run.gate(fail_under=7, caller=caller)
    elapsed = time.time() - t0
    results = run.results()  # typed RunResultRow objects (P1.5)
    ratings = [r.rating for r in results if r.rating is not None]
    jaccards = [r.jaccard_similarity for r in results if r.jaccard_similarity is not None]
    avg = sum(ratings) / len(ratings) if ratings else None
    jac = sum(jaccards) / len(jaccards) if jaccards else float("nan")
    print(f"  {caller}: exit={gate.exit_code} avg_rating={avg:.2f} "
          f"jaccard_avg={jac:.2f} wall={elapsed:.1f}s cases={len(results)}")
    return gate.exit_code, avg


print("\n=== healthy build (expect exit 0) ===")
exit_ok, avg_a = gated_run(healthy_agent, "buyer-gate-healthy")

print("=== healthy build again (judge variance probe) ===")
exit_ok2, avg_b = gated_run(healthy_agent, "buyer-gate-healthy-2")

print("=== regressed build (expect exit 1) ===")
exit_bad, avg_bad = gated_run(regressed_agent, "buyer-gate-regressed")

variance = abs(avg_a - avg_b) if avg_a is not None and avg_b is not None else None
print(f"\njudge variance across identical runs: {variance:.2f} rating points")

ok = exit_ok == 0 and exit_ok2 == 0 and exit_bad == 1 and (variance is not None and variance <= 1.5)
print("UC2 PASS" if ok else "UC2 FAIL")
