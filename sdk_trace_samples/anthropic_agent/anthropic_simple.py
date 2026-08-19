import os

import anthropic
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


agentx_client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
claude_client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)


@agentx_client.tracer.trace(
    "claude-agent", framework="anthropic", model="claude-sonnet-4-6"
)
def run(query: str) -> str:
    msg = claude_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": query}],
    )
    return msg.content[0].text


run("How do I cancel my subscription?")
agentx_client.tracer.flush(timeout=10)
