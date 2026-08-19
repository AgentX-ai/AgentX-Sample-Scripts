import os

import requests
from dotenv import load_dotenv
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

tracer = client.tracer


@tracer.trace("customer-support-agent", framework="TV feeder", model="gpt-4o")
def handle(query: str) -> str:
    # your own agent builder logic here
    return f"Custom test response to query: {query}"


handle("How do I reset my password?")
client.tracer.flush()
print("done")
