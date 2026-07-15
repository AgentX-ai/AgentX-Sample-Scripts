import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from agentx import AgentX

load_dotenv()

# client = AgentX.from_env()
client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)

llm = ChatOpenAI(model="gpt-4o", api_key=os.getenv("OPENAI_API_KEY"))


@client.tracer.trace("support-agent", framework="langchain", model="gpt-4o")
def handle(query: str) -> str:
    return llm.invoke([HumanMessage(content=query)]).content


handle("How do I reset my password?")
client.tracer.flush(timeout=10)
