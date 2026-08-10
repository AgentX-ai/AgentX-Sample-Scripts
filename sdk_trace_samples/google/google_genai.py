import os
import requests
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.google_genai import patch_genai_client
from google import genai
from google.genai import types

load_dotenv()

# No workspace_id, the API key alone selects the project. BASE_URL defaults to the local engine;
# the key itself is fetched from the unauthenticated bootstrap endpoint the same way the dashboard
# does on load, so nothing needs to be hand-copied into .env for this to run.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
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
