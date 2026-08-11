"""
Prompt registry example - self-host only.

Demonstrates AgentX's prompt registry (client.evaluations.prompts): the external-agent analog to
native Autotune. AgentX doesn't own your agent's code, so instead of branching/merging a config it
becomes your prompt's source of truth - you fetch it at runtime, use it as your agent's actual
system prompt, and tag your evaluation runs with which version you used. A human then reviews an
LLM-judge-proposed rewrite (based on your worst-rated real examples) in the dashboard and decides
whether to publish it as a new version. Nothing ever gets rewritten automatically.

Requires a self-host engine (AgentX-trace-eval/engine) running locally - the prompt registry has
no equivalent on the hosted SaaS backend. Defaults to http://localhost:4700/api/v1, override with
AGENTX_SELFHOST_BASE_URL if the engine is running elsewhere.
"""

import os
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.evaluations.client import AgentXEvaluationsError
from agentx.evaluations.models import Dataset, EvaluationCase, Prompt, Report
from agentx.evaluations.runner import EvaluationRunContext

load_dotenv()

# No workspace_id, the API key alone selects the project. BASE_URL defaults to the local engine;
# the key itself is fetched from the unauthenticated bootstrap endpoint the same way the dashboard
# does on load, so nothing needs to be hand-copied into .env for this to run.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

PROMPT_NAME = "support-agent-system-prompt"

# --- Step 1: register the prompt (once) --------------------------------------------------------
# In practice you'd register a prompt once (or via the dashboard), then every later run of your
# real agent just calls prompts.get(). Get-then-create here only so this script is runnable
# standalone and safe to re-run without erroring on "already exists".
try:
    prompt: Prompt = client.evaluations.prompts.get(PROMPT_NAME)
    print(f"Using existing prompt: {prompt.name} v{prompt.version}")
except AgentXEvaluationsError:
    prompt = client.evaluations.prompts.create(
        name=PROMPT_NAME,
        text=(
            "You are a helpful, empathetic customer support agent. Always acknowledge the "
            "customer's concern first, ask clarifying questions when the request is ambiguous, "
            "and offer a concrete next step or solution."
        ),
        description="System prompt for the support agent example.",
    )
    print(f"Created prompt: {prompt.name} v{prompt.version}")


# --- Step 2: use prompt.text as your agent's actual system prompt -------------------------------
# This is the whole point: nothing here is hardcoded. If someone publishes a new version in the
# dashboard, the next run of your agent picks it up automatically via prompts.get().
def support_agent(case: EvaluationCase) -> Dict[str, Any]:
    resp = oai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": case.query},
        ],
    )
    return {"output": resp.choices[0].message.content, "metadata": {"model": resp.model}}


# --- Step 3: build a small dataset to evaluate this prompt against ------------------------------
dataset: Dataset = (
    client.evaluations.datasets.builder(
        name="Prompt Registry Example Dataset",
        number_of_requests=1,
        acceptance_criteria="Empathetic, asks clarifying questions, offers a concrete next step.",
        rejection_criteria="Dismissive, ignores the question, or invents a policy that wasn't stated.",
    )
    .add_case(
        query="My order hasn't arrived after 2 weeks, what do I do?",
        expected_results="Apologizes, asks for the order number, offers to check status or refund/replace.",
    )
    .add_case(
        query="Can I get a refund on a digital product I already downloaded?",
        expected_results="Explains the digital-goods policy clearly without inventing details.",
    )
    .publish()
)
print(f"Published dataset: {dataset.id}")


# --- Step 4: run the evaluation, tagging it with the prompt name + version ----------------------
# This is what makes the prompt registry's "worst rated examples" / "propose improvement" feature
# (dashboard: Governance -> Evaluate -> Prompts) able to find this run afterward: the engine reads
# subject.metadata.promptName off every evaluation run and matches it against each registered
# prompt's name - no dedicated SDK field, just this one convention.
run_context: EvaluationRunContext = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={
            "kind": "custom_agent",
            "displayName": "Support Agent (prompt registry example)",
            "framework": "openai",
            "metadata": {
                "promptName": prompt.name,
                "version": f"{prompt.name}@v{prompt.version}",
            },
        },
    )
    .execute(support_agent)
    .finalize()
)
report: Report = run_context.analyze()

# .analyze()'s holistic report generation (POST /runs/{id}/analyze) isn't implemented on every
# self-host engine version yet - per-question results are still scored and saved either way
# (that happened during .execute()/.finalize() above), so this only guards the summary print.
if report.average_rating is not None:
    print(f"\nAverage rating: {report.average_rating:.2f}")
else:
    print("\n(No holistic report from this engine - per-question ratings were still recorded; see the dashboard.)")
if report.dashboard_url:
    print(f"Dashboard: {report.dashboard_url}")

print(
    "\nNext: open Governance -> Evaluate -> Prompts in the self-host dashboard, select "
    f'"{prompt.name}", and click "Propose improvement". The engine gathers every tagged run\'s '
    "worst-rated examples, asks an LLM judge to rewrite the prompt to fix the recurring issues, "
    "and shows you the diff - publishing it as a new version is a separate, explicit click. "
    "Nothing is ever rewritten automatically."
)
