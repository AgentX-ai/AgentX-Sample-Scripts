"""
UC5 - RAG evaluation: catch a broken retriever deterministically, no judge required.

Buyer questions:
  - Can a dataset pin the EXPECTED retrieval context per question, and score the retriever's
    actual chunks against it with a cheap deterministic metric (Jaccard)?
  - Do eval results link their traces (retrieval spans visible for debugging)?
  - Does the score actually separate a correct retrieval from a miss?

The retriever under test has a realistic bug: warranty questions fall through to the shipping
chunk. Refund retrieval is correct. Expected: high context score on refund, ~0 on warranty.
"""

import os
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4791/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"UC5 rag {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

REFUND = "Refund policy: refunds within 30 days of delivery for unused items; shipping fees non-refundable."
WARRANTY = "Warranty: 2-year limited warranty covering manufacturing defects; repairs free incl. return shipping."
SHIPPING = "Shipping: US, Canada, EU. 3-5 business days domestic, 7-10 international."

dataset = (
    client.evaluations.datasets.builder(
        name="Buyer RAG retriever gate",
        evaluation_criteria="Answers must come from the retrieved context.",
    )
    .add_case(query="What is the refund window?", expected_retrieval_context=[REFUND])
    .add_case(query="Is there a warranty on the espresso machine?", expected_retrieval_context=[WARRANTY])
    .publish()
)


def retrieve(query):
    if "refund" in query.lower():
        return [REFUND]
    return [SHIPPING]  # bug: warranty questions land on the shipping chunk


def rag_agent(case):
    chunks = retrieve(case.query)
    answer = f"Per policy: {chunks[0][:70]}..."
    with client.tracer.trace("buyer-rag-agent", input={"query": case.query}, sync=True, monitor=False) as span:
        with client.tracer.trace_retrieval("kb_search", query=case.query) as r:
            r.output = chunks
            r.doc_count = len(chunks)
        span.output = answer
    return {"output": answer, "retrieval_context": chunks, "trace_id": span.trace_id}


run = (
    client.evaluations.run(dataset_id=dataset.id, subject={"kind": "custom_agent", "displayName": "buyer-rag"})
    .execute(rag_agent)
    .finalize()
)

scores = {}
traces_linked = 0
for result in run.results():  # typed rows (P1.5) - no raw REST needed anymore
    q = result.question_text or ""
    if result.trace_id:
        traces_linked += 1
    for scorer in result.code_scorer_results or []:
        if "context" in (scorer.get("name") or "").lower():
            scores[q[:20]] = scorer.get("score")
            print(f"  {q[:45]:45s} context-match={scorer.get('score')}")

refund_score = next((v for k, v in scores.items() if "refund" in k.lower()), None)
warranty_score = next((v for k, v in scores.items() if "warranty" in k.lower()), None)
print(f"traces linked to results: {traces_linked}/2")

ok = (
    refund_score is not None and refund_score >= 0.9
    and warranty_score is not None and warranty_score <= 0.2
    and traces_linked == 2
)
print("\nUC5 PASS" if ok else "\nUC5 FAIL")
