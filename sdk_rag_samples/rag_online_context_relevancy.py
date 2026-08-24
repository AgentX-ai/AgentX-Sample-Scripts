"""
Online RAG evaluation #2: Context Relevancy - catching a broken retriever that
Faithfulness alone would miss.

The companion to rag_online_faithfulness.py, run on the same kind of traffic. The warranty
question retrieves a shipping chunk (nothing about warranties exists in the corpus). The
generator honestly declines to answer, so Faithfulness scores it 10 - the RESPONSE is fine.
Context Relevancy judges the retrieved chunks against the query, ignoring the response
entirely, so the same trace scores ~0 and (being below the alert threshold) raises a
high-severity Signal in the triage queue. That component split - generator fine, retriever
broken - is the whole point of the RAG metric pack.

Install:
    pip install agentx-python langchain langchain-core langchain-openai

Run against a local self-host engine (default http://localhost:4700/api/v1, override with
AGENTX_SELFHOST_BASE_URL):
    OPENAI_API_KEY=... AGENTX_API_KEY=... python rag_online_context_relevancy.py
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


# Same broken-by-omission retriever as rag_online_faithfulness.py: no warranty
# document exists, so warranty questions retrieve the shipping chunk.

SHIPPING_DOC = (
    "Shipping policy: we ship to the US, Canada, and the EU. Standard delivery takes 3-5 "
    "business days domestically and 7-10 business days internationally. Orders over $50 "
    "ship free."
)


class WrongChunkRetriever(BaseRetriever):
    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        return [Document(page_content=SHIPPING_DOC, metadata={"source": "shipping"})]


prompt = ChatPromptTemplate.from_template(
    "You are a support agent. Answer using ONLY the context below.\n\n"
    "Context:\n{context}\n\nQuestion: {question}"
)
chain = (
    {"context": WrongChunkRetriever() | (lambda docs: "\n\n".join(d.page_content for d in docs)),
     "question": RunnablePassthrough()}
    | prompt
    | ChatOpenAI(model="gpt-4o-mini", temperature=0)
    | StrOutputParser()
)

# ---------------------------------------------------------------------------
# Online evaluator on the seeded "RAG: Context Relevancy" config. severity
# "high" because irrelevant retrieval is the failure this corpus actually has.
# ---------------------------------------------------------------------------

# The seeded template IS a judge scorer - switching its live scoring on is one sparse update.
evaluator = next(s for s in client.monitor.judge_scorers.list() if s.name == "RAG: Context Relevancy")
client.monitor.judge_scorers.update(
    evaluator.id,
    online={"enabled": True, "sampleRate": 1.0, "alertThreshold": 5, "severity": "high"},
)
print(f"Live scoring enabled on seeded judge scorer '{evaluator.name}' ({evaluator.id})")

handler = AgentXCallbackHandler(tracer=client.tracer, name="rag-support-agent")
question = "Does the espresso machine come with a warranty?"
answer = chain.invoke(question, config={"callbacks": [handler]})
print(f"\nQ: {question}\nA: {answer[:160]}")

client.tracer.flush(timeout=10)

# Wait for the ingest-time judgment, then show the rating and the Signal it raised.
events = []
for _ in range(30):
    events = client.monitor.judge_scorers.events(evaluator.id, window="24h")
    if events:
        break
    time.sleep(3)

for event in events:
    print(f"\nrating {event.rating} | {(event.justification or '')[:220]}")

for signal in client.monitor.signals.list(severity="high", limit=10):
    if evaluator.name in signal.summary:
        print(f"\nSIGNAL: {signal.severity} | {signal.summary[:200]}")

client.monitor.judge_scorers.update(evaluator.id, online={"enabled": False})
print(f"\nPaused live scoring on {evaluator.id} - re-enable it on the Scorers page")
