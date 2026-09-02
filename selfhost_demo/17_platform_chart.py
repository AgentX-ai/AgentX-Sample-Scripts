"""
Platform-agnostic tracing, on one chart: agents from several platforms report into the same
engine, and Monitor's new "Platforms" card shows the mix (root traces per platform over time).

Three ways a trace gets its platform label, all shown here:
  1. framework="..." - explicit, ANY string works, including platforms AgentX has no
     integration for ("acme-inhouse-runner" below charts like a first-class citizen),
  2. integrations stamp their literal automatically (langchain, crewai, openai-agents, ... -
     simulated here with the explicit parameter so no framework installs are needed),
  3. nothing at all - the SDK auto-detects the one orchestration framework imported in the
     process, and when it can't say for sure the trace charts as "Other / custom" rather than
     being mislabeled or hidden.

No LLM key needed - the "agents" are simulated with plain traces.

Run it, then open Governance > Monitor: the Platforms card stacks one color per platform
(same colors as the framework badges in Observe), and `framework:<name>` in the filter box
scopes every card to one platform.
"""

import os

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


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
client.ping()

# One support fleet, four platforms - the realistic mid-migration enterprise picture. In your
# code the label comes free from the integration (AgentXCallbackHandler -> "langchain", the
# CrewAI observer -> "crewai", ...); framework= here just stands in for that.
FLEET = [
    ("order-tracker", "langchain", "Where is order #4471?", "In transit, arriving Thursday."),
    ("order-tracker", "langchain", "Did #9203 ship?", "Yes - it left the warehouse this morning."),
    ("refund-crew", "crewai", "Refund my duplicate charge", "Refund issued; 3-5 business days."),
    ("triage-assistant", "openai-agents", "My login loops forever", "Cleared the stale session - try again."),
    ("legacy-bot", "acme-inhouse-runner", "What are your hours?", "We answer 24/7."),  # custom platform
]

for agent, platform, query, reply in FLEET:
    with client.tracer.trace(agent, framework=platform, input={"query": query}, sync=True) as span:
        span.output = reply

# And one trace with NO framework anywhere: it still ingests, still counts, and buckets as
# "Other / custom" on the chart - unlabeled is never mislabeled, and never invisible.
with client.tracer.trace("mystery-agent", input={"query": "ping"}, sync=True) as span:
    span.output = "pong"

metrics = client.monitor.metrics(window="1h")
counts = {f["name"]: f["count"] for f in metrics.get("frameworks", [])}
print("Platforms in the last hour (Monitor > Platforms chart):")
for name, count in counts.items():
    print(f"  {name:24s} {count}")

expected = {"langchain", "crewai", "openai-agents", "acme-inhouse-runner", "other"}
missing = expected - set(counts)
if missing:
    raise SystemExit(f"FAIL: expected platforms missing from metrics: {sorted(missing)}")

print("\nAll platforms attributed. Open Governance > Monitor - the Platforms card shows the mix,")
print("and typing framework:langchain in the filter box scopes every card to that platform.")
