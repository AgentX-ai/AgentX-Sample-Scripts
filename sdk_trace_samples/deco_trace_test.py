import os

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

# Explicit key
client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)

tracer = client.tracer


@tracer.trace("customer-support-agent", framework="TV feeder", model="gpt-4o")
def handle(query: str) -> str:
    # your own agent builder logic here
    return f"Custom test response to query: {query}"


handle("How do I reset my password?")
client.tracer.flush()
print("done")
