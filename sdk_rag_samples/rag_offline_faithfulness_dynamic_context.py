"""
Offline RAG evaluation: a dataset run where each result carries the context the agent
ACTUALLY retrieved for that case, not a stale pin on the dataset.

A real RAG agent retrieves per query, so the agent function returns
`{"output": ..., "retrieval_context": [chunks...]}` and the Faithfulness judge grades each
answer against that run's own chunks. The two cases are built to split:

  - The refund case answers "90 days, no questions asked" while its own retrieved chunk
    says 30 days for unused items - contradicting your own context scores 0.
  - The shipping case answers exactly what its chunk says - faithful, scores 10.

Context precedence in offline runs: `retrieval_context` on the returned result wins, else
the linked trace's retrieval spans (if you return `trace_id`), else the dataset case's
pinned `retrievalContext`.

Install:
    pip install agentx-python

Run against a local self-host engine (default http://localhost:4700/api/v1, override with
AGENTX_SELFHOST_BASE_URL):
    OPENAI_API_KEY=... AGENTX_API_KEY=... python rag_offline_faithfulness_dynamic_context.py
    (OPENAI_API_KEY - or the engine's configured judge key - is what the judge model uses)
"""

import os

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


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)

faithfulness = next(
    s for s in client.evaluations.settings.list() if s.name == "RAG: Faithfulness"
)

dataset = (
    client.evaluations.datasets.builder(name="RAG Faithfulness (dynamic context)")
    .add_case(query="What is the refund window?")
    .add_case(query="Do you ship internationally?")
    .publish()
)
print(f"Dataset {dataset.id}: {len(dataset.questions)} cases")

# ---------------------------------------------------------------------------
# A canned "agent" standing in for retriever + generator, so the faithful /
# unfaithful split is deterministic. In a real agent this is your retriever
# output and your model's answer.
# ---------------------------------------------------------------------------

DEMO = {
    "What is the refund window?": (
        ["Refunds are available within 30 days of delivery for unused items."],
        "Our refund window is 90 days, no questions asked.",  # contradicts its own chunk
    ),
    "Do you ship internationally?": (
        ["We ship to the US, Canada, and the EU. International orders take 7-10 business days."],
        "Yes - we ship to the US, Canada, and the EU; delivery takes 7-10 business days.",
    ),
}


def rag_agent(case):
    chunks, answer = DEMO[case.query]
    # Trace the case so the result links its trace (View Trace in the run detail table):
    # sync=True populates span.trace_id before the block exits, monitor=False keeps this
    # run's own judge as the only scorer (no double-judging at ingest). The recorded
    # retrieval also means the engine could pull context from the trace - the explicit
    # retrieval_context returned below simply takes precedence over it.
    with client.tracer.trace(
        "rag-demo-offline", input={"query": case.query}, sync=True, monitor=False
    ) as span:
        with client.tracer.trace_retrieval("kb_search", query=case.query) as r:
            r.output = chunks
            r.doc_count = len(chunks)
        span.output = answer
    return {"output": answer, "retrieval_context": chunks, "trace_id": span.trace_id}


run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        evaluation_settings_id=faithfulness.id,
        subject={"kind": "custom_agent", "displayName": "rag-demo-offline"},
    )
    .execute(rag_agent)
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
    print(f"  rating {result.get('rating')}: {str(result.get('justification'))[:180]}")
