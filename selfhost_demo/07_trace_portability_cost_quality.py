"""
Model portability: take a real captured conversation and replay just the input side of it against
alternative models, scoring each candidate's answer against the original with the same LLM judge
used everywhere else, so you can see the cost/quality tradeoff of switching models before you
actually switch, on your own real traffic instead of a generic benchmark.

Input-only replay of a captured trace, not a full agent re-run (self-host doesn't own your agent's
code/tools), explicit and per-trace, never automatic. No dedicated SDK method exists yet for this
(dashboard-managed today), so this script calls the same REST API the dashboard uses.
"""

import os

import requests
from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.integrations.openai import patch_openai_client

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


API_KEY = local_api_key()
HEADERS = {"x-api-key": API_KEY}

client = AgentX(api_key=API_KEY, base_url=BASE_URL)
oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# Without this, the baseline row's cost below comes back "n/a": estimateCostUSD needs real
# inputTokens/outputTokens on the trace, which a manually-wrapped raw OpenAI call never reports
# unless the client is patched (see 02_trace_your_agent.py's docstring for why).
patch_openai_client(oai, client.tracer)


# --- Step 1: a real trace on a pricier model -----------------------------------------------------
# The OpenAI call must happen INSIDE the `with` block, patch_openai_client only merges token
# counts into whichever span is active at call time (tracer.current_span); called before the
# block opens, there's no active span yet and the patched client sends its own independent trace
# instead, which isn't the one span.trace_id below refers to.
query = "Write a 2-sentence apology to a customer whose order arrived damaged, offering a replacement."
with client.tracer.trace(
    "support-agent",
    input={"query": query},
    framework="openai",
    model="gpt-4o",
    sync=True,
) as span:
    resp = oai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a helpful support agent."}, {"role": "user", "content": query}],
    )
    span.output = resp.choices[0].message.content

print(f"Baseline (gpt-4o): {resp.choices[0].message.content}")
print(f"trace_id: {span.trace_id}")


# --- Step 2: candidate models to compare against -------------------------------------------------
# From the dashboard-editable portability_models table, seeded with a small default set on first
# boot. gpt-4o-mini and claude-haiku-4-5 are both meaningfully cheaper than gpt-4o.
candidates = ["gpt-4o-mini", "claude-haiku-4-5"]

result = requests.post(
    f"{BASE_URL}/agent-monitoring/traces/{span.trace_id}/portability",
    headers=HEADERS,
    json={"modelIds": candidates},
    timeout=60,
).json()

print(f"\n{'model':<20} {'rating':>8} {'cost ($)':>10} {'latency (ms)':>14}  output")
for r in result["results"]:
    tag = "baseline" if r["isBaseline"] else "candidate"
    cost = f"{r['estimatedCostUSD']:.5f}" if r["estimatedCostUSD"] is not None else "n/a"
    print(f"{r['model']:<20} {r['rating']:>8} {cost:>10} {str(r['latencyMs']):>14}  {(r['outputText'] or '')[:70]!r}  [{tag}]")

print(
    "\nSame idea works across any trace already in the system, not just ones sent moments ago, "
    "the dashboard's Trace tab has a 'Compare models' action on every trace detail view."
)
