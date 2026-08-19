import os
import requests
from dotenv import load_dotenv
from crewai import Agent, Task, Crew
from agentx import AgentX

load_dotenv()

# CrewAI's own LLM resolver (crewai/utilities/llm_utils.py) reads a bare BASE_URL env var as a
# generic "point my LLM calls at this OpenAI-compatible endpoint" override - same generic name
# this repo's .env uses for AgentX's own (hosted-platform) base URL, an unrelated collision. Clear
# it before constructing anything, so CrewAI's default LLM still calls the real OpenAI API instead
# of trying to connect to whatever that value happened to be.
os.environ.pop("BASE_URL", None)

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


@client.tracer.trace("research-crew", framework="crewai")
def run_crew(topic: str) -> str:
    agent = Agent(role="Researcher", goal=f"Research {topic}", backstory="...")
    task = Task(description=f"Research {topic}", agent=agent, expected_output="...")
    crew = Crew(agents=[agent], tasks=[task])
    return str(crew.kickoff())


run_crew("What is the refund policy?")
client.tracer.flush(timeout=10)
