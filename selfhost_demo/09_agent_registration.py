"""
Self-host has no "create agent" step, no form to fill in first. An "agent" is a real row in its
own registry (core/monitor/agents.ts in AgentX-trace-eval), but you never have to populate it
explicitly: the moment a trace lands under a `name` Governance hasn't seen before, that name gets
its own agent row automatically (core/monitor/agents.ts's resolveAgentId -- creates one on first
use, resolves to the same one on every later trace under that same name).

That means the `name` you pass as the first argument to client.tracer.trace(name, ...) *is* the
agent's identity everywhere in Governance from then on -- Overview's agent table and autotune
scoping key off this exact string. Get it right once at the call site (e.g. "support-agent",
"billing-triage") and every other self-host feature keyed to "which agent" just works, no separate
registration call to remember, and no way for the dashboard and your instrumentation to drift out
of sync since there's only ever one source of truth: the traces themselves. (Monitoring coverage --
sample rate, retention, latency threshold -- is project-level now, set once for every agent via
Platform Settings, not something this per-agent identity affects.)

This script proves it by hitting GET /agent-monitoring/agents (the same endpoint Overview's agent
table itself polls) before and after sending traces:
  1. A brand-new agent name doesn't exist yet.
  2. One trace under that name -> it exists.
  3. A second trace under the *same* name -> still one agent, not a duplicate.
  4. A trace under a *different* new name -> now there are two.

GET /agent-monitoring/agents is dashboard-only (no dedicated SDK method for it, same as a few
other things this folder's README calls out), so this uses `requests` directly for that one call,
the exact endpoint Overview's agent table calls.
"""

import os

import requests
from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    key = os.getenv("AGENTX_API_KEY")
    if not key:
        raise SystemExit(
            "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup."
        )
    return key


API_KEY = local_api_key()
HEADERS = {"x-api-key": API_KEY}
client = AgentX(api_key=API_KEY, base_url=BASE_URL)


def agent_names() -> list[str]:
    """Same endpoint Overview's agent table itself polls -- no SDK method for it, dashboard-only read."""
    resp = requests.get(f"{BASE_URL}/agent-monitoring/agents", headers=HEADERS, timeout=5)
    resp.raise_for_status()
    return [a["name"] for a in resp.json()["agents"]]


# Unique per run so this script can be re-run against a non-fresh install without "already exists"
# noise from a previous run's demo agents.
suffix = os.urandom(3).hex()
agent_a = f"registration-demo-a-{suffix}"
agent_b = f"registration-demo-b-{suffix}"

before = agent_names()
print(f"Agents before any traces ({len(before)} total): {before}")
assert agent_a not in before


# --- Step 1: one trace under a brand-new name -> that name becomes an agent ----------------------
# sync=True blocks until the engine has actually ingested the trace, needed here so the very next
# GET reflects it immediately -- the default fire-and-forget mode sends on a background thread and
# would otherwise race this script's own read.
with client.tracer.trace(agent_a, input={"query": "hello"}, sync=True) as span:
    span.output = "hi there"

after_first = agent_names()
print(f"\nAfter one trace under {agent_a!r}: {after_first}")
assert agent_a in after_first


# --- Step 2: a second trace under the *same* name -> still one agent, not a duplicate -------------
with client.tracer.trace(agent_a, input={"query": "how are you"}, sync=True) as span:
    span.output = "doing well"

after_second = agent_names()
print(f"After a second trace under the same name: {after_second}")
assert after_second.count(agent_a) == 1


# --- Step 3: a trace under a *different* new name -> now there are two ---------------------------
with client.tracer.trace(agent_b, input={"query": "different agent"}, sync=True) as span:
    span.output = "separate identity"

after_third = agent_names()
print(f"\nAfter a trace under a second, different name {agent_b!r}: {after_third}")
assert agent_a in after_third and agent_b in after_third

print(
    "\nNo `create agent` call happened anywhere above -- both rows now visible in the dashboard's "
    "Governance > Overview agent table were created purely by the `name` passed to "
    "client.tracer.trace(...). Whatever string you use there is the agent identity every other "
    "self-host feature (autotune scoping, project-level monitoring coverage) keys off going "
    "forward -- pick it deliberately and keep it consistent across your own instrumentation."
)
