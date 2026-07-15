import os
import asyncio
from dotenv import load_dotenv
from agents import Agent, Runner
from agentx import AgentX

load_dotenv()

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)
agent = Agent(name="Support", instructions="You are a helpful support agent.")


@client.tracer.trace("openai-support-agent", framework="openai-agents", model="gpt-4o")
async def run(query: str) -> str:
    result = await Runner.run(agent, query)
    return result.final_output


asyncio.run(run("How do I cancel my subscription?"))
client.tracer.flush(timeout=10)
