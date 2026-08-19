"""
Online RAG evaluation #1: Faithfulness on live traffic, with zero-config context capture.

What this demonstrates, end to end:

1. A real LangChain RAG chain (retriever + gpt-4o-mini) traced with AgentXCallbackHandler.
   The retriever call is recorded as a retrieval span on the trace - nothing RAG-specific is
   passed to AgentX by the application code.
2. An online evaluator referencing the seeded "RAG: Faithfulness" config, sampling 100% of
   this agent's traffic. At ingest, the judge reads the retrieved chunks straight from the
   trace's retrieval spans (no metadata.retrievalContext anywhere) and scores whether every
   claim in the response is grounded in them.
3. The interesting outcome: one query has NO answer in the corpus (warranty - the retriever
   fetches an unrelated chunk). A well-behaved model declines to invent an answer, and
   Faithfulness correctly scores that honesty 10/10. Faithfulness judges the GENERATOR, not
   the retriever - pair it with rag_online_context_relevancy.py to catch the broken
   retrieval on the same traffic.

Install:
    pip install agentx-python langchain langchain-core langchain-openai

Run against a local self-host engine (default http://localhost:4700/api/v1, override with
AGENTX_SELFHOST_BASE_URL):
    OPENAI_API_KEY=... AGENTX_API_KEY=... python rag_online_faithfulness.py
"""

import os
import time
from typing import List

from dotenv import load_dotenv
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI

from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler

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


# ---------------------------------------------------------------------------
# The RAG agent under evaluation: a tiny keyword retriever over a 3-doc support
# corpus. There is deliberately NO warranty document - warranty questions fall
# through to the shipping chunk, a realistic retrieval miss.
# ---------------------------------------------------------------------------

CORPUS = {
    "refund": (
        "Refund policy: refunds are available within 30 days of delivery for unused items in "
        "their original packaging. Shipping fees are non-refundable. Refunds are issued to the "
        "original payment method within 5-7 business days of the return being received."
    ),
    "shipping": (
        "Shipping policy: we ship to the US, Canada, and the EU. Standard delivery takes 3-5 "
        "business days domestically and 7-10 business days internationally. Orders over $50 "
        "ship free."
    ),
    "returns": (
        "Return process: start a return from your order page to generate a prepaid label. "
        "Drop the package at any carrier location. You will receive an email when the "
        "warehouse receives it."
    ),
}


class TinyRetriever(BaseRetriever):
    """Naive keyword router - the point is that its failure mode is real: queries about
    topics missing from the corpus (warranty) still retrieve SOMETHING, just the wrong
    thing."""

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        q = query.lower()
        if "refund" in q or "money back" in q:
            key = "refund"
        elif "return" in q and "process" in q:
            key = "returns"
        else:
            key = "shipping"  # the catch-all: warranty questions land here
        return [Document(page_content=CORPUS[key], metadata={"source": key})]


prompt = ChatPromptTemplate.from_template(
    "You are a support agent. Answer using ONLY the context below.\n\n"
    "Context:\n{context}\n\nQuestion: {question}"
)

retriever = TinyRetriever()
chain = (
    {"context": retriever | (lambda docs: "\n\n".join(d.page_content for d in docs)),
     "question": RunnablePassthrough()}
    | prompt
    | ChatOpenAI(model="gpt-4o-mini", temperature=0)
    | StrOutputParser()
)


# ---------------------------------------------------------------------------
# Online evaluator: the seeded "RAG: Faithfulness" config, 100% sampling so the
# demo traffic is definitely judged. sample_rate=1 means one judge LLM call per
# trace - use something like 0.1 on real traffic.
# ---------------------------------------------------------------------------

faithfulness = next(
    s for s in client.evaluations.settings.list() if s.name == "RAG: Faithfulness"
)
evaluator = client.monitor.online_evaluators.builder(
    name="RAG Faithfulness (sample)",
    evaluation_settings_id=faithfulness.id,
    sample_rate=1.0,
    alert_threshold=5,  # a response scoring below 5 raises a Signal
).publish()
print(f"Online evaluator {evaluator.id} referencing config '{faithfulness.name}'")

handler = AgentXCallbackHandler(tracer=client.tracer, name="rag-support-agent")
for question in [
    "What is your refund window and how long do refunds take?",
    "Does the espresso machine come with a warranty?",  # not in the corpus
]:
    answer = chain.invoke(question, config={"callbacks": [handler]})
    print(f"\nQ: {question}\nA: {answer[:160]}")

client.tracer.flush(timeout=10)

# ---------------------------------------------------------------------------
# Judging happens at ingest - poll the evaluator's events until both traces
# are scored, then read the judge's justification. Expect ratings of 10 even
# for the warranty question IF the model honestly said the context does not
# cover warranty: faithfulness rewards refusing to hallucinate.
# ---------------------------------------------------------------------------

events = []
for _ in range(30):
    events = client.monitor.online_evaluators.events(evaluator.id, window="24h")
    if len(events) >= 2:
        break
    time.sleep(3)

for event in events:
    print(f"\nrating {event.rating} | {(event.justification or '')[:220]}")

# Pause the evaluator so this sample does not keep judging (and paying for)
# unrelated live traffic. Delete it instead if you do not want to keep it.
client.monitor.online_evaluators.update(evaluator.id, enabled=False)
print(f"\nPaused evaluator {evaluator.id} - re-enable it in Monitor -> Online Evaluators")
