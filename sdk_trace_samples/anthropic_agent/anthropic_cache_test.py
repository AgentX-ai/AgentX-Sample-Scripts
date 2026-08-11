import os

import requests
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.anthropic import patch_anthropic_client
import anthropic

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


agentx_client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

patch_anthropic_client(
    client,
    tracer=agentx_client.tracer,
    name="claude-cache-demo",
    metadata={"env": "cache-token-demo"},
    session_id="session-cache-demo-001",
)

# Anthropic only caches a prompt block once it clears a per-model token floor (2048 for Haiku
# models) - repeat a long passage well past that, and mark it cacheable with cache_control.
SYSTEM_PROMPT = (
    "You are a support agent for AgentX. Follow these policies exactly.\n\n"
    + ("Policy: Refunds are issued within 30 days of purchase if the product is unused. " * 400)
)

system_block = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]

model = "claude-haiku-4-5-20251001"

# Call 1: no cache entry exists yet for this system block -> Anthropic writes it to cache.
# cache_creation_input_tokens > 0, cache_read_input_tokens == 0.
r1 = client.messages.create(
    model=model,
    max_tokens=200,
    system=system_block,
    messages=[{"role": "user", "content": "How do I cancel my subscription?"}],
)
print("Call 1 (cache write):", r1.usage)

# Call 2: same system block within Anthropic's ~5-minute cache TTL -> served from cache instead of
# reprocessed. cache_read_input_tokens > 0, cache_creation_input_tokens == 0.
r2 = client.messages.create(
    model=model,
    max_tokens=200,
    system=system_block,
    messages=[{"role": "user", "content": "What is your refund policy?"}],
)
print("Call 2 (cache read):", r2.usage)

agentx_client.tracer.flush(timeout=10)
print("\nDone. Check the trace detail in AgentX self-host (session-cache-demo-001) for the Cached tokens tile.")
