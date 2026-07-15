import os
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.openai_agents import AgentXTracingProcessor
from agents import Agent, Runner, add_trace_processor, function_tool

load_dotenv()

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    workspace_id=os.getenv("WORKSPACE_ID"),
    base_url=os.getenv("BASE_URL"),
)

add_trace_processor(
    AgentXTracingProcessor(
        tracer=client.tracer,
        metadata={"env": "production"},
        session_id="session-001",
    )
)


@function_tool
def get_policy(topic: str) -> str:
    """Return the company policy for a given topic."""
    db = {
        "cancel": "Go to Account → Subscription → Cancel.",
        "trial": "14-day free trial, no credit card required.",
        "refund": "Full refund within 30 days.",
    }
    return db.get(topic.lower(), "No policy found.")


agent = Agent(
    name="support-agent",
    instructions="You are a helpful support agent. Use get_policy to look up policies.",
    tools=[get_policy],
    # model="gpt-4o-mini",
)

result = Runner.run_sync(agent, "How do I cancel my subscription?")
print(result.final_output)

client.tracer.flush(timeout=10)
