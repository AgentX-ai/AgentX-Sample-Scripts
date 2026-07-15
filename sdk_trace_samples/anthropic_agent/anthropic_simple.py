import os

import anthropic
from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

agentx_client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)
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
