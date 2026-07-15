import os
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.google_adk import AgentXADKPlugin
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types

load_dotenv()

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)


def get_policy(topic: str) -> dict:
    """Return the company policy for a given topic."""
    db = {
        "cancel": "Go to Account → Subscription → Cancel.",
        "trial": "14-day free trial, no credit card required.",
        "refund": "Full refund within 30 days.",
    }
    return {"result": db.get(topic.lower(), "No policy found.")}


agent = Agent(
    name="support_agent",
    instruction="You are a helpful support agent. Use get_policy to look up policies.",
    tools=[FunctionTool(func=get_policy)],
)

runner = Runner(
    agent=agent,
    app_name="support-app",
    session_service=InMemorySessionService(),
    plugins=[
        AgentXADKPlugin(
            tracer=client.tracer,
            name="support-agent",
            metadata={"env": "production"},
        )
    ],
)

session = runner.session_service.create_session_sync(
    app_name="support-app", user_id="user-1"
)
for event in runner.run(
    user_id="user-1",
    session_id=session.id,
    new_message=types.Content(
        role="user", parts=[types.Part(text="How do I cancel my subscription?")]
    ),
):
    if event.is_final_response():
        print(event.content.parts[0].text)

client.tracer.flush(timeout=10)
