import os

from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)

handler = AgentXCallbackHandler(
    tracer=client.tracer,
    name="support-chain",
    session_id="session-001",
)

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=os.getenv("OPENAI_API_KEY"),
)
prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "You are a helpful support agent."),
        ("human", "{question}"),
    ]
)
chain = prompt | llm | StrOutputParser()

result = chain.invoke(
    {"question": "How do I cancel my subscription?"},
    config={"callbacks": [handler]},
)
print(result)

client.tracer.flush(timeout=10)
