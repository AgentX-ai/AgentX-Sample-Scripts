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
    name="support-agent-tool-fail",  # custom name for the agent
    session_id="session-001",  # custom session id for the agent
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


@tool
def policy_lookup(topic: str) -> str:
    """Look up a company policy by topic."""
    # Simulate a downstream failure (e.g. DB/API outage) so the tool call
    # errors out and the failure is captured in the trace.
    raise RuntimeError(
        f"Policy service unavailable: failed to look up policy for '{topic}'."
    )


llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=OPENAI_API_KEY,
)
agent = create_agent(
    llm,
    tools=[policy_lookup],
    system_prompt="You are a helpful support agent. Always use the policy_lookup tool to answer policy questions.",
)

question = "How do I cancel my subscription?"

try:
    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"callbacks": [handler]},
    )
    print(result["messages"][-1].content)
except Exception as exc:
    print(f"Agent run failed as expected due to tool error: {exc!r}")

client.tracer.flush(timeout=10)
