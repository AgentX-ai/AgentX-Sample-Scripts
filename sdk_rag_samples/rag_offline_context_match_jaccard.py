"""
Deterministic retriever regression check: expected retrieval context vs. actual, scored
with token-level Jaccard similarity - no LLM judge call involved.

Each dataset case pins `expected_retrieval_context`: the chunk(s) a CORRECT retriever
should fetch for that query. At scoring time the engine compares them against what the
agent actually retrieved (the `retrieval_context` it returned, or the linked trace's
retrieval spans) and reports a "Context match (jaccard)" scorer row with the 0-1
similarity. It rides the same codeScorerResults surface as trajectory match, so it shows
up per-result in the dashboard with no extra setup.

Use it as the cheap, deterministic layer under the judge-based RAG metrics: run it on
every retriever/chunking/embedding change to catch regressions for free, and keep
Context Relevancy / Faithfulness (LLM judges, see the other samples in this folder) for
the semantic judgment Jaccard cannot make.

The two cases split by construction:
  - The refund case retrieves exactly the expected chunk - similarity 1.0.
  - The warranty case expects a warranty chunk but the broken retriever returns the
    shipping chunk - similarity near 0.

Install:
    pip install agentx-python

Run against a local self-host engine (default http://localhost:4700/api/v1, override with
AGENTX_SELFHOST_BASE_URL):
    AGENTX_API_KEY=... python rag_offline_context_match_jaccard.py
    (the run's regular LLM judge still grades each response - the Context match row is
    the deterministic part)
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

REFUND_CHUNK = (
    "Refund policy: refunds are available within 30 days of delivery for unused items in "
    "their original packaging. Shipping fees are non-refundable."
)
WARRANTY_CHUNK = (
    "Warranty: every espresso machine includes a 2-year limited warranty covering "
    "manufacturing defects. Warranty repairs are free including return shipping."
)
SHIPPING_CHUNK = (
    "Shipping policy: we ship to the US, Canada, and the EU. Standard delivery takes 3-5 "
    "business days domestically and 7-10 business days internationally."
)

dataset = (
    client.evaluations.datasets.builder(
        name="Retriever regression (jaccard)",
        evaluation_criteria="The response should answer the question using the retrieved context.",
    )
    .add_case(
        query="What is the refund window?",
        expected_retrieval_context=[REFUND_CHUNK],
    )
    .add_case(
        query="Does the espresso machine come with a warranty?",
        expected_retrieval_context=[WARRANTY_CHUNK],
    )
    .publish()
)
print(f"Dataset {dataset.id}: {len(dataset.questions)} cases")

# ---------------------------------------------------------------------------
# The agent under test, with the same broken-by-omission retriever as the
# online samples: warranty queries fall through to the shipping chunk.
# ---------------------------------------------------------------------------


def retrieve(query: str) -> list:
    if "refund" in query.lower():
        return [REFUND_CHUNK]
    return [SHIPPING_CHUNK]  # the catch-all: warranty questions land here


def rag_agent(case):
    chunks = retrieve(case.query)
    answer = f"Based on our policy: {chunks[0][:80]}..."
    return {"output": answer, "retrieval_context": chunks}


run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "displayName": "rag-retriever-regression"},
    )
    .execute(rag_agent)
    .finalize()
)

detail = requests.get(
    f"{BASE_URL}/evaluate/{run.run_id}",
    headers={"x-api-key": client.api_key},
    timeout=10,
).json()
for result in detail.get("results", []):
    print(f"\nQ: {result.get('questionText')}")
    for scorer in result.get("codeScorerResults") or []:
        score = scorer.get("score")
        shown = f"{score:.2f}" if isinstance(score, (int, float)) else "SKIPPED"
        print(f"  {scorer.get('name')}: {shown} - {scorer.get('reasoning') or scorer.get('error')}")
