import os

from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain.agents import create_agent

load_dotenv()

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)

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
