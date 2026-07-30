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

Run:
    AGENTX_API_KEY=... OPENAI_API_KEY=... python langchain_billing_dispute_eval.py
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler
from agentx.evaluations.models import EvaluationCase, Report
from agentx.evaluations.runner import EvaluationRunContext

load_dotenv()

TRACE_SAMPLE_DIR = (
    Path(__file__).resolve().parents[1] / "sdk_trace_samples" / "langchain"
)
sys.path.insert(0, str(TRACE_SAMPLE_DIR))

from langchain_billing_dispute_investigation import investigate

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)

handler = AgentXCallbackHandler(
    tracer=client.tracer,
    name="support-agent-billing",  # custom name for the agent
    session_id="session-001",  # custom session id for the agent
)

dataset_id = "6a628215ab10849d2abf4000"  # "Billing dispute" dataset
eval_settings_id = "6a6186c5df2374920f2b15cc"


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

# Available immediately, no .analyze() call needed just to see the score.
print(
    f"Average rating: {run_context.average_rating:.2f} ({run_context.rated_count} rated)"
)

report: Report = run_context.analyze()
print(f"Dashboard: {report.dashboard_url}")

# Every investigate() call above already used sync=True, so nothing is queued at this point;
# this is just a defensive no-op safety net in case anything else on this client ever sends async.
client.tracer.flush(timeout=15)
