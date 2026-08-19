"""
Offline evaluation of a Databricks-hosted agent (Agent Bricks or Mosaic AI Agent Framework).

The agent stays where it is - deployed on a Databricks Model Serving endpoint - and this script
drives it through an AgentX dataset run: each case invokes the endpoint, the judge scores the
answer (trajectory-aware when traces are linked), and cases with `expected_tools` get a
deterministic Trajectory match verdict.

Pair it with either live-trace path so results link to real traces:
  - push: enable_mlflow_export(...) on the endpoint (OTLP -> AgentX), or
  - pull: `agentx-databricks sync --experiment-id <id> --since 24h` after the run.

Env:
    AGENTX_API_KEY        AgentX project key (engine prints it at startup)
    DATABRICKS_HOST       e.g. https://adb-1234.5.azuredatabricks.net
    DATABRICKS_TOKEN      PAT with query access to the serving endpoint
    DATABRICKS_ENDPOINT   serving endpoint name (e.g. "agents_main-support_agent")

Run:
    python databricks_agent_eval.py
"""

import os

import requests
from dotenv import load_dotenv

from agentx import AgentX

load_dotenv()

AGENTX_BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
DATABRICKS_HOST = os.environ["DATABRICKS_HOST"].rstrip("/")
DATABRICKS_ENDPOINT = os.environ["DATABRICKS_ENDPOINT"]
DATABRICKS_TOKEN = os.environ["DATABRICKS_TOKEN"]

client = AgentX(api_key=os.environ["AGENTX_API_KEY"], base_url=AGENTX_BASE_URL)


def invoke_databricks_agent(question: str) -> str:
    """One call to the Model Serving endpoint - agent-framework (messages) request shape."""
    resp = requests.post(
        f"{DATABRICKS_HOST}/serving-endpoints/{DATABRICKS_ENDPOINT}/invocations",
        headers={"Authorization": f"Bearer {DATABRICKS_TOKEN}"},
        json={"messages": [{"role": "user", "content": question}]},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    # ChatAgent/ResponsesAgent responses put the answer in messages[-1]; older pyfunc agents
    # in predictions. Take whichever is present.
    if isinstance(data.get("messages"), list) and data["messages"]:
        return str(data["messages"][-1].get("content", ""))
    if "choices" in data:
        return str(data["choices"][0]["message"]["content"])
    return str(data.get("predictions", data))


def run_agent(case):
    answer = invoke_databricks_agent(case.query)
    return {"output": answer}


dataset = (
    client.evaluations.datasets.builder(
        name="Databricks Agent Eval",
        description="Offline evaluation cases for the Databricks-served support agent.",
        acceptance_criteria="Accurate, grounded answers; refund statements must follow policy.",
        rejection_criteria="Invented details, policy violations, or unanswered questions.",
    )
    .add_case(
        query="I want a refund for order A-1001, it arrived two weeks ago.",
        expected_results="Confirms eligibility within the 30-day window and starts the refund flow.",
        # With linked traces (push or pull path above), the engine trajectory-matches the tools
        # the deployed agent actually called:
        expected_tools=["lookup_order", "refund_policy"],
        trajectory_match_mode="unordered",
    )
    .add_case(
        query="Where is my order A-1002 right now?",
        expected_results="Reports the current shipping status for A-1002.",
    )
    .publish()
)

run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "displayName": DATABRICKS_ENDPOINT},
    )
    .execute(run_agent)
    .finalize()
)
print(f"Scored {run.rated_count} results - average {run.average_rating}")
