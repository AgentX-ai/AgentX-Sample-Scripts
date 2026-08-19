import os
import requests
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.google_adk import AgentXADKPlugin
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types

load_dotenv()

# No workspace_id, the API key alone selects the project. BASE_URL defaults to the local engine;
# the key itself is fetched from the unauthenticated bootstrap endpoint the same way the dashboard
# does on load, so nothing needs to be hand-copied into .env for this to run.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    key = os.getenv("AGENTX_API_KEY")
    if not key:
        raise SystemExit(
            "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup."
        )
    return key


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)


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
