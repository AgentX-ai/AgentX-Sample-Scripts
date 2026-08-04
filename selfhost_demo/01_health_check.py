"""
Run this first, always. Confirms the self-host engine is up and reachable before you're standing
in front of an audience relying on it, and prints the info you'll want on screen anyway (dashboard
URL, local API key, what's already in the database).

No AGENTX_API_KEY needed: the local key is generated once on first boot and readable from an
unauthenticated bootstrap endpoint, the same way the dashboard itself fetches it on load.
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
ENGINE_ROOT = BASE_URL.removesuffix("/api/v1")


def fail(message: str) -> None:
    raise SystemExit(
        f"\n{message}\n\n"
        f"Start the engine first, e.g.:\n"
        f"  AGENTX_HOME=/tmp/agentx-demo ./dist/agentx-server --dev\n"
        f"(or `yarn dev` from AgentX-trace-eval/engine if running from source)\n"
    )


print(f"Checking {BASE_URL} ...")

try:
    health = requests.get(f"{ENGINE_ROOT}/health", timeout=5)
except requests.exceptions.ConnectionError:
    fail(f"Can't reach the engine at {ENGINE_ROOT}.")
else:
    if health.status_code != 200:
        fail(f"Engine responded but not healthy (status {health.status_code}).")

bootstrap = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
bootstrap.raise_for_status()
api_key = bootstrap.json()["apiKey"]

headers = {"x-api-key": api_key}
prompts = requests.get(f"{BASE_URL}/evaluate/prompts", headers=headers, timeout=5).json()
datasets = requests.get(f"{BASE_URL}/evaluate/evaluationSettings", headers=headers, timeout=5).json()

print("\nEngine is up.")
print(f"  Base URL:       {BASE_URL}")
print(f"  Local API key:  {api_key}")
print(f"  Dashboard:      {ENGINE_ROOT} (if started with --dev, it should already be open)")
print(f"  Prompts:        {len(prompts.get('prompts', []))} registered")
print(f"  Datasets/configs: {len(datasets.get('evaluationSettings', []))} present")
print(
    "\nIf those counts look higher than expected, this isn't a fresh install, either that's "
    "fine for this demo, or restart with a clean AGENTX_HOME (see this folder's README)."
)
