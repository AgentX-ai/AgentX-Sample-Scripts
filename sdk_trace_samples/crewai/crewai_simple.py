import os
from dotenv import load_dotenv
from crewai import Agent, Task, Crew
from agentx import AgentX

load_dotenv()

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    workspace_id=os.getenv("WORKSPACE_ID"),
    base_url=os.getenv("BASE_URL"),
)


@client.tracer.trace("research-crew", framework="crewai")
def run_crew(topic: str) -> str:
    agent = Agent(role="Researcher", goal=f"Research {topic}", backstory="...")
    task = Task(description=f"Research {topic}", agent=agent, expected_output="...")
    crew = Crew(agents=[agent], tasks=[task])
    return str(crew.kickoff())


run_crew("What is the refund policy?")
client.tracer.flush(timeout=10)
