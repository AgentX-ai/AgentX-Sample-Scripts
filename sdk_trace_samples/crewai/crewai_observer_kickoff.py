import os

from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.crewai import AgentXCrewObserver
from crewai import Agent, Task, Crew

load_dotenv()

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    workspace_id=os.getenv("WORKSPACE_ID"),
    base_url=os.getenv("BASE_URL"),
)

observer = AgentXCrewObserver(
    tracer=client.tracer,
    name="support-crew",
    metadata={"env": "production"},
)

researcher = Agent(
    role="Policy Researcher",
    goal="Find relevant company policies",
    backstory="You look up policies from the knowledge base.",
    verbose=False,
)
writer = Agent(
    role="Support Writer",
    goal="Write clear, friendly support responses",
    backstory="You turn policy details into helpful customer replies.",
    verbose=False,
)
crew = Crew(
    agents=[researcher, writer],
    tasks=[
        Task(
            description="Look up the cancellation policy and summarize it.",
            expected_output="A concise policy statement.",
            agent=researcher,
        ),
        Task(
            description="Write a friendly support reply about cancelling a subscription.",
            expected_output="A clear, helpful response to send to the customer.",
            agent=writer,
        ),
    ],
    verbose=False,
)

result = observer.kickoff(crew, inputs={"query": "cancel subscription"})
print(result.raw)

client.tracer.flush(timeout=10)
