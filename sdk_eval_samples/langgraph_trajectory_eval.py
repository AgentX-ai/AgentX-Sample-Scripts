"""
Trajectory evaluation of a LangGraph agent - the full agentic-workflow eval loop.

What this demonstrates, end to end:

1. A LangGraph react agent (two tools) traced with AgentXCallbackHandler. The handler records
   the run as a real span tree: graph nodes ("agent", "tools") become child spans, and every
   LLM call / tool call is parented under the node that ran it - open the trace in the
   dashboard and the Execution Timeline / Graph view shows the actual graph trajectory.
2. A dataset whose cases declare `expected_tools` - the tool calls a correct run should make.
   At scoring time the engine matches each result's linked trace against them and reports a
   pass/fail "Trajectory match" scorer row per result (strict / unordered / superset / subset
   modes, agentevals semantics).
3. Trajectory-aware LLM judging: because each result returns its `trace_id`, the judge prompt
   includes the agent's actual execution steps (tools, order, failures), so the evaluation
   criteria below can score HOW the answer was produced, not just the final text.

Install:
    pip install agentx-python langchain langgraph langchain-openai

Run against a local self-host engine (default http://localhost:4700/api/v1, override with
AGENTX_SELFHOST_BASE_URL):
    OPENAI_API_KEY=... python langgraph_trajectory_eval.py
"""

import os

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)


# ---------------------------------------------------------------------------
# The agent under evaluation: a small LangGraph react agent for order support.
# ---------------------------------------------------------------------------

ORDERS = {
    "A-1001": {"status": "delivered", "delivered_days_ago": 12, "total_usd": 89.00},
    "A-1002": {"status": "in_transit", "delivered_days_ago": None, "total_usd": 42.50},
}


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order's status, delivery date, and total by its id (e.g. A-1001)."""
    order = ORDERS.get(order_id.strip().upper())
    if not order:
        return f"No order found with id {order_id!r}."
    return str(order)


@tool
def refund_policy() -> str:
    """Return the store's refund policy."""
    return (
        "Refunds are available within 30 days of delivery for unused items. "
        "Shipping costs are non-refundable. In-transit orders can be cancelled instead."
    )


agent = create_agent(
    ChatOpenAI(model="gpt-4o-mini", temperature=0),
    tools=[lookup_order, refund_policy],
    system_prompt=(
        "You are an order-support agent. Always look the order up before answering, and "
        "check the refund policy before making any refund statement."
    ),
)

handler = AgentXCallbackHandler(client.tracer, name="langgraph-order-support")


def run_agent(case):
    """One eval case -> one traced LangGraph run, linked to its result via trace_id."""
    # The handler folds the LangGraph run (and its span tree) into this enclosing span;
    # sync=True populates span.trace_id before the block exits, and returning it is what lets
    # the engine trajectory-match and trajectory-judge this result. monitor=False keeps the
    # run's own judge as the only scorer (no double-judging at ingest).
    with client.tracer.trace(
        "langgraph-order-support", input={"query": case.query}, sync=True, monitor=False
    ) as span:
        result = agent.invoke(
            {"messages": [("user", case.query)]},
            config={"callbacks": [handler]},
        )
        answer = result["messages"][-1].content
        span.output = answer
    return {"output": answer, "trace_id": span.trace_id}


# ---------------------------------------------------------------------------
# Dataset: each case declares the tools a correct run should call.
# ---------------------------------------------------------------------------

dataset = (
    client.evaluations.datasets.builder(
        name="LangGraph Order Support - Trajectory Eval",
        description="Order support scenarios with expected tool-call trajectories.",
        acceptance_criteria=(
            "Grounded in tool results: order details come from lookup_order, refund statements "
            "come from refund_policy, and the final answer follows from what the tools returned."
        ),
        rejection_criteria=(
            "Invented order details or refund terms, refund promises without checking the "
            "policy, or answers produced without consulting the relevant tool."
        ),
        evaluation_criteria=(
            "Judge the execution trajectory as well as the answer: each tool call should be a "
            "sensible choice for the step, arguments grounded in the request, no unnecessary or "
            "repeated calls, and the final answer consistent with the tool outputs."
        ),
    )
    .add_case(
        query="I want a refund for order A-1001, it arrived almost two weeks ago.",
        expected_results=(
            "Order A-1001 was delivered 12 days ago, within the 30-day refund window, so it is "
            "eligible for a refund of $89.00 (shipping non-refundable) if the item is unused."
        ),
        # A correct run must consult BOTH tools; order doesn't matter for this case.
        expected_tools=["lookup_order", "refund_policy"],
        trajectory_match_mode="unordered",
    )
    .add_case(
        query="Where is my order A-1002 right now?",
        expected_results="Order A-1002 is currently in transit.",
        # A status question needs exactly one lookup - calling refund_policy too would be a
        # wasted step, which "subset" mode treats as a failure ("no unexpected calls").
        expected_tools=["lookup_order"],
        trajectory_match_mode="subset",
    )
    .publish()
)
print(f"Dataset {dataset.id}: {len(dataset.questions)} cases")

# ---------------------------------------------------------------------------
# Run + inspect: trajectory match rows land in codeScorerResults per result.
# ---------------------------------------------------------------------------

run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "displayName": "langgraph-order-support"},
    )
    .execute(run_agent)
    .finalize()
)
print(
    f"Scored {run.rated_count} results - average {run.average_rating}, "
    f"min {run.min_rating}, max {run.max_rating}"
)

detail = requests.get(
    f"{BASE_URL}/evaluate/{run.run_id}",
    headers={"x-api-key": client.api_key},
    timeout=10,
).json()
for result in detail.get("results", []):
    print(f"\nQ: {result.get('questionText')}")
    print(f"  judge rating : {result.get('rating')}")
    for scorer in result.get("codeScorerResults") or []:
        verdict = "PASS" if scorer.get("score") == 1 else "FAIL" if scorer.get("score") == 0 else "SKIPPED"
        print(f"  {scorer.get('name')} : {verdict} - {scorer.get('reasoning') or scorer.get('error')}")

client.tracer.flush(timeout=10)
