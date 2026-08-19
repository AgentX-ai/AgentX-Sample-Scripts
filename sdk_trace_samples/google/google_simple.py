import os
import asyncio
import requests
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from agentx import AgentX

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

agent = Agent(
    name="support_agent",
    model="gemini-3.5-flash",
    instruction="You are a helpful support agent.",
)

runner = Runner(
    agent=agent,
    app_name="support-app",
    session_service=InMemorySessionService(),
)


@client.tracer.trace(
    "google-support-agent", framework="google-adk", model="gemini-3.5-flash"
)
async def run(query: str) -> str:
    session = runner.session_service.create_session_sync(
        app_name="support-app", user_id="user-1"
    )
    for event in runner.run(
        user_id="user-1",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=query)]),
    ):
        if event.is_final_response():
            return event.content.parts[0].text
    return ""


asyncio.run(run("How do I cancel my subscription?"))
client.tracer.flush(timeout=10)
