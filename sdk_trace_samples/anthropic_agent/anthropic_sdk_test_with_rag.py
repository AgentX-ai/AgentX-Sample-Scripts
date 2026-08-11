import os

import requests
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.anthropic import patch_anthropic_client
import anthropic

load_dotenv()

# No workspace_id, the API key alone selects the project. BASE_URL defaults to the local engine;
# the key itself is fetched from the unauthenticated bootstrap endpoint the same way the dashboard
# does on load, so nothing needs to be hand-copied into .env for this to run.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


agentx_client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

patch_anthropic_client(
    client,
    tracer=agentx_client.tracer,
    name="claude-support-agent-rag",
    metadata={"env": "production"},
    session_id="session-xyz-789",
)

# Small knowledge base used as the retrieval corpus.
knowledge_base = [
    "To cancel your subscription, go to Account → Subscription → Cancel.",
    "We offer a 14-day free trial, no credit card required.",
    "Full refunds are available within 30 days of purchase.",
    "You can upgrade or downgrade your plan at any time.",
    "Product A has xxx features.",
    "Product B has yyy features.",
]


def retrieve(query: str, k: int = 2) -> list[str]:
    """Naive keyword-overlap retriever that returns the top-k docs for a query."""
    query_words = set(query.lower().split())

    def score(doc: str) -> int:
        return len(query_words & set(doc.lower().split()))

    ranked = sorted(knowledge_base, key=score, reverse=True)
    return ranked[:k]


MODEL = "claude-haiku-4-5-20251001"

question = "How do I cancel my subscription?"

# trace_retrieval/record_retrieval only ever record a child span of the currently active span
# (Tracer.current_span) - a no-op with nothing to attach to if called outside a `with
# tracer.trace(...)` block, see that method's own docstring. So the retrieval and the LLM call
# both need to happen inside one orchestrator span, same pattern
# anthropic_sdk_test_with_tool.py uses, for the retrieval step to actually show up on the trace
# instead of silently going nowhere.
with agentx_client.tracer.trace(
    "claude-support-agent-rag",
    input=question,
    framework="anthropic",
    metadata={"env": "production"},
    session_id="session-xyz-789",
) as span:
    # RAG: retrieve context up front and inject it into the prompt.
    # The retrieval happens in plain Python (no framework hook the Anthropic
    # patch can see), so it's recorded manually to show up in the trace's
    # performance summary.
    with agentx_client.tracer.trace_retrieval("kb_search", query=question) as r:
        docs = retrieve(question, k=2)
        r.doc_count = len(docs)
    context = "\n".join(f"- {doc}" for doc in docs)

    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system="You are a helpful support agent. Answer using only the provided context.",
        messages=[
            {
                "role": "user",
                "content": (
                    f"Use the following retrieved context to answer the question.\n\n"
                    f"Context:\n{context}\n\n"
                    f"Question: {question}"
                ),
            }
        ],
    )

    for block in response.content:
        if block.type == "text":
            span.output = block.text
            print(block.text)

agentx_client.tracer.flush(timeout=10)
