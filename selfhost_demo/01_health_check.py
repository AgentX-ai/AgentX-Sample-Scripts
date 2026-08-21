"""
Run this first, always. Confirms the self-host engine is up, the API key is accepted, and
prints the info you'll want on screen anyway (dashboard URL, what's already in the database).

`client.ping()` is the same fail-fast check you'd put at the top of any long-running service:
one cheap authenticated call that raises AgentXConnectionError (engine unreachable) or
AgentXAuthError (key rejected) with an actionable message, instead of the SDK's normal lazy
behavior where a bad URL or key only surfaces once something actually sends.
"""

import os

from dotenv import load_dotenv

from agentx import AgentX
from agentx.exceptions import AgentXConnectionError, AgentXAuthError

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")

api_key = os.getenv("AGENTX_API_KEY")
if not api_key:
    raise SystemExit(
        "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup "
        "(for Docker: docker logs <container> | grep 'API key')."
    )

client = AgentX(api_key=api_key, base_url=BASE_URL)

print(f"Checking {BASE_URL} ...")
try:
    client.ping()
except AgentXConnectionError as exc:
    raise SystemExit(
        f"\n{exc}\n\n"
        "Start the engine first, e.g.:\n"
        "  AGENTX_HOME=/tmp/agentx-demo ./dist/agentx-server --dev\n"
        "(or `yarn dev` from AgentX-trace-eval/engine if running from source)\n"
    )
except AgentXAuthError as exc:
    raise SystemExit(f"\n{exc}")

prompts = client.evaluations.prompts.list()
configs = client.evaluations.settings.list()

print("\nEngine is up and the key is accepted.")
print(f"  Base URL:       {BASE_URL}")
print(f"  Local API key:  {api_key}")
print(f"  Dashboard:      {BASE_URL.removesuffix('/api/v1')} (if started with --dev, it should already be open)")
print(f"  Prompts:        {len(prompts)} registered")
print(f"  Grading configs: {len(configs)} present")
print(
    "\nIf those counts look higher than expected, this isn't a fresh install, either that's "
    "fine for this demo, or restart with a clean AGENTX_HOME (see this folder's README)."
)
