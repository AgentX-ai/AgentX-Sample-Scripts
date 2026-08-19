"""
Evaluates the LangChain billing-dispute investigation agent defined in
sdk_trace_samples/langchain/langchain_billing_dispute_investigation.py against a dataset of
billing-dispute customer messages.

That script is a standalone trace demo (its own AgentX client, its own AgentXCallbackHandler,
and an `investigate(user_message, client, handler, sync=False) -> (response, trace_id)` entry
point guarded by `if __name__ == "__main__":`), not an installed package. This eval imports
`investigate` directly by path so it exercises the exact same orchestrator, tools, and sub-agents
rather than reimplementing them, and calls it with sync=True so each result links to its trace.

Install:
    pip install agentx-python langchain langchain-openai

Run against a local self-host engine (default http://localhost:4700/api/v1, override with
AGENTX_SELFHOST_BASE_URL):
    OPENAI_API_KEY=... python langchain_billing_dispute_eval.py
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict

import requests
from dotenv import load_dotenv

from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler
from agentx.evaluations.models import Dataset, EvaluationCase, EvaluationSettings
from agentx.evaluations.runner import EvaluationRunContext

load_dotenv()

TRACE_SAMPLE_DIR = (
    Path(__file__).resolve().parents[1] / "sdk_trace_samples" / "langchain"
)
sys.path.insert(0, str(TRACE_SAMPLE_DIR))

from langchain_billing_dispute_investigation import investigate

# No workspace_id, the API key alone selects the project. BASE_URL defaults to the local engine;
# the key itself is fetched from the unauthenticated bootstrap endpoint the same way the dashboard
# does on load, so nothing needs to be hand-copied into .env for this to run.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    key = os.getenv("AGENTX_API_KEY")
    if not key:
        raise SystemExit(
            "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup."
        )
    return key


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)

handler = AgentXCallbackHandler(
    tracer=client.tracer,
    name="support-agent-billing",  # custom name for the agent
    session_id="session-001",  # custom session id for the agent
)

# No dataset/eval-settings id is portable across installs, so both are built fresh here rather
# than referencing a fixed hosted-platform id - investigate()'s own mock backend (customer,
# subscription, invoice, refund ledger) is fixed for every call regardless of the case, only the
# wording of the customer's message varies per case, see billing_dispute_agent's own comment below.
dataset: Dataset = (
    client.evaluations.datasets.builder(
        name="Billing Dispute Investigation Eval",
        description="Cases for the LangChain billing-dispute investigation agent.",
        number_of_requests=1,
        acceptance_criteria=(
            "Warm, concise, and factual: explains what was found and what was done, references "
            "the relevant policy in plain language, and never exposes internal reasoning, "
            "confidence scores, or system details."
        ),
        rejection_criteria=(
            "No invented policy details, no promising an outcome that contradicts the "
            "investigation, no exposing internal risk/eligibility scoring."
        ),
    )
    .add_case(
        query=(
            "I was charged $499 for an annual plan renewal yesterday, I tried to cancel one day "
            "before the renewal, but the page wasn't working. Please refund and ensure I won't be "
            "billed again!"
        ),
        expected_results=(
            "Acknowledges the failed cancellation attempt, explains a refund was issued (or "
            "escalated for review), confirms the subscription is canceled, and confirms auto-renew "
            "is disabled."
        ),
    )
    .add_case(
        query="Why was I charged for a renewal when I'm sure I canceled in time?",
        expected_results=(
            "Investigates the cancellation timing against the renewal date before concluding, and "
            "explains the outcome without blaming the customer."
        ),
    )
    .publish()
)
dataset_id = dataset.id

eval_settings: EvaluationSettings = client.evaluations.settings.builder(
    name="Billing Dispute Investigation Eval Config",
    number_of_requests=1,
    acceptance_criteria=(
        "Warm, concise, and factual: explains what was found and what was done, references the "
        "relevant policy in plain language, and never exposes internal reasoning, confidence "
        "scores, or system details."
    ),
    rejection_criteria=(
        "No invented policy details, no promising an outcome that contradicts the investigation, "
        "no exposing internal risk/eligibility scoring."
    ),
).publish()
eval_settings_id = eval_settings.id


def billing_dispute_agent(case: EvaluationCase) -> Dict[str, Any]:
    # sync=True blocks until AgentX has ingested the orchestrator trace, so investigate() gets
    # trace_id back before returning. Passing trace_id through in the result dict below is what
    # links this eval result to the trace, so its dashboard row gets a "View trace" action opening
    # the full Execution Timeline instead of just the score.
    # The mock backend inside investigate() (customer, subscription, invoice, refund ledger) is
    # fixed for every call; only the wording of the customer's message varies per case.
    output, trace_id = investigate(
        user_message=case.query, client=client, handler=handler, sync=True
    )
    return {"output": output, "metadata": {"framework": "langchain"}, "trace_id": trace_id}


run_context: EvaluationRunContext = (
    client.evaluations.run(
        dataset_id=dataset_id,
        subject={
            "kind": "custom_agent",
            "displayName": "Billing Dispute Investigation Agent",
            "framework": "langchain",
            "runtime": "local",
        },
        evaluation_settings_id=eval_settings_id,
    )
    .execute(billing_dispute_agent)
    .finalize()
)

# run_context.average_rating reads a `liveStatistics` field the hosted SaaS API returns but
# self-host's engine doesn't populate yet, so it comes back None here even though every result was
# genuinely scored (self-host's holistic .analyze() report endpoint isn't implemented either, same
# gap). Pull the per-question ratings directly from the run instead and average them here - same
# workaround as selfhost_demo/03_evaluate_with_a_dataset.py.
# run_context._run.run_id: no public accessor for the run id exists on EvaluationRunContext yet.
run_detail = requests.get(
    f"{BASE_URL}/evaluate/{run_context._run.run_id}",
    headers={"x-api-key": client.api_key},
    timeout=10,
).json()
ratings = [r["rating"] for r in run_detail["results"] if r.get("rating") is not None]
if ratings:
    print(f"Average rating: {sum(ratings) / len(ratings):.2f} ({len(ratings)} rated)")
else:
    print("(No ratings yet.)")
print(f"Dashboard: {BASE_URL.removesuffix('/api/v1')}")

# Every investigate() call above already used sync=True, so nothing is queued at this point;
# this is just a defensive no-op safety net in case anything else on this client ever sends async.
client.tracer.flush(timeout=15)
