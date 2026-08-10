import os

import requests
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
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
    name="support-agent",  # custom name for the agent
    session_id="session-001",  # custom session id for the agent
)


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
    api_key=os.getenv("OPENAI_API_KEY"),
)
agent = create_agent(
    llm,
    tools=[policy_lookup],
    system_prompt="You are a helpful support agent.",
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": "How do I cancel my subscription?"}]},
    config={"callbacks": [handler]},
)
print(result["messages"][-1].content)

client.tracer.flush(timeout=10)
