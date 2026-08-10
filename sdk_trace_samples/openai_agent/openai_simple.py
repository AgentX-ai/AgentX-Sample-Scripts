import os
import asyncio
import requests
from dotenv import load_dotenv

# pip install openai-agents
from agents import Agent, Runner
from agentx import AgentX

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
agent = Agent(name="Support", instructions="You are a helpful support agent.")


@client.tracer.trace("openai-support-agent", framework="openai-agents", model="gpt-4o")
async def run(query: str) -> str:
    result = await Runner.run(agent, query)
    return result.final_output


asyncio.run(run("How do I cancel my subscription?"))
client.tracer.flush(timeout=10)
