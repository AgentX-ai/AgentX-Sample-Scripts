import os

import requests
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.tools import tool
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain.agents import create_agent

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

handler = AgentXCallbackHandler(
    tracer=client.tracer,
    name="support-agent-rag",  # custom name for the agent
    session_id="session-001",  # custom session id for the agent
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Build a small knowledge base and index it into an in-memory vector store.
knowledge_base = [
    Document(page_content="Need to look up a policy to find the cancellation guide."),
    Document(page_content="Product A has xxx features."),
    Document(page_content="Product B has yyy features."),
    Document(page_content="You can upgrade or downgrade your plan at any time."),
]

embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY)
vector_store = InMemoryVectorStore.from_documents(knowledge_base, embedding=embeddings)
retriever = vector_store.as_retriever(search_kwargs={"k": 2})


@tool
def policy_lookup(topic: str) -> str:
    """Look up a company policy by topic."""
    db = {
        "cancel": "Go to Account → Subscription → Cancel.",
        "trial": "14-day free trial, no credit card required.",
        "refund": "Full refund within 30 days.",
    }
    for key, val in db.items():
        if key in topic.lower():
            return val
    return "No policy found."


llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=OPENAI_API_KEY,
)
agent = create_agent(
    llm,
    tools=[policy_lookup],
    system_prompt="You are a helpful support agent.",
)

question = "How do I cancel my subscription?"

# RAG always happens: retrieve context up front and inject it into the prompt.
# Pass the handler so the retrieval step is captured in the trace.
docs = retriever.invoke(question, config={"callbacks": [handler]})
context = "\n".join(f"- {doc.page_content}" for doc in docs)

result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Use the following retrieved context to answer the question.\n\n"
                    f"Context:\n{context}\n\n"
                    f"Question: {question}"
                ),
            }
        ]
    },
    config={"callbacks": [handler]},
)
print(result["messages"][-1].content)

client.tracer.flush(timeout=10)
