import os

from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.anthropic import patch_anthropic_client
import anthropic

load_dotenv()

agentx_client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    workspace_id=os.getenv("WORKSPACE_ID"),
    base_url=os.getenv("BASE_URL"),
)
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
