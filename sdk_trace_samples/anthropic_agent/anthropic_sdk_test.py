import os

import requests
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.anthropic import patch_anthropic_client
import anthropic

load_dotenv()

# No workspace_id, the API key alone selects the project. BASE_URL defaults to the local engine;
# the key itself is fetched from the unauthenticated bootstrap endpoint the same way the dashboard
# does on load, so nothing needs to be hand-copied into .env for this to run.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


agentx_client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

patch_anthropic_client(
    client,
    tracer=agentx_client.tracer,
    name="claude-support-agent",
    metadata={"env": "production"},
    session_id="session-xyz-789",
)

# Regular call — traced automatically
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=256,
    messages=[{"role": "user", "content": "How do I cancel my subscription?"}],
)

# Streaming call — also traced automatically
with client.messages.stream(
    model="claude-haiku-4-5-20251001",
    max_tokens=256,
    messages=[{"role": "user", "content": "What is your refund policy?"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)

# Both calls above trace fire-and-forget on a background thread; without this, the process can
# exit before the second (streaming) call's trace finishes sending.
agentx_client.tracer.flush(timeout=10)
