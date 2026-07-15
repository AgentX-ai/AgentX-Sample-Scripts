import os
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.google_genai import patch_genai_client
from google import genai
from google.genai import types

load_dotenv()

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)
genai_client = genai.Client()

patch_genai_client(
    genai_client,
    tracer=client.tracer,
    name="gemini-support-agent",
    metadata={"env": "production"},
)

# Regular call — traced automatically
response = genai_client.models.generate_content(
    contents="How do I cancel my subscription?",
    model="gemini-3.5-flash",
)
print(response.text)

# # Streaming call — also traced automatically
# for chunk in genai_client.models.generate_content_stream(
#     contents="What is your refund policy?",
#     model="gemini-3.5-flash",
# ):
#     print(chunk.text, end="", flush=True)

client.tracer.flush(timeout=10)
