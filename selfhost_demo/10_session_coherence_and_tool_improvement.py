"""
Session-level evaluation, end to end: ingest one multi-turn session whose individual replies each
look fine but whose conversation as a whole falls apart (a failed tool call, goal drift, a policy
contradiction, redone work), then let AgentX catch and fix it at all three layers:

  1. Session coherence: one judge call over the WHOLE assembled session (not per-trace), rating
     0-10 and pinpointing the exact span where the conversation lost the thread. This is the
     answer to "every reply looked fine, so why was the session bad?" that per-trace evaluators
     can't give by construction.
  2. Tool improvement: the failed search_orders call becomes evidence against that tool's
     registered schema, and a judge proposes a rewrite of the tool DEFINITION itself.
  3. Prompt improvement: the same session's low-scored traffic (tagged metadata.promptName)
     becomes evidence for the prompt registry's propose loop, same as
     05_prompt_registry_autotune_loop.py but fed by session traffic instead of a curated run.

Every turn is a REAL traced LLM call (patched OpenAI client, same pattern as
02_trace_your_agent.py), so each turn's trace carries real tokens, a real LLM child span, and -
for the order-lookup turns - a real tool-call child span recorded via trace_tool_call. The
conversation stays deterministically incoherent by instructing the model to deliver each turn's
scripted reply verbatim: real spans and usage, scripted content, since a well-prompted model
would not reliably reproduce this failure mode on its own (same reasoning as 06's bad-response
trace). Needs OPENAI_API_KEY in .env for the turn completions; the ENGINE also needs a judge key
for the coherence check, online evaluator scoring, and both propose calls.

Set PUBLISH = True to actually publish both proposed rewrites at the end.
"""

import os
import time

import requests
from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.evaluations.client import AgentXEvaluationsError
from agentx.evaluations.models import Prompt
from agentx.integrations.openai import patch_openai_client

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
PUBLISH = False


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
oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
patch_openai_client(oai, client.tracer)

AGENT_NAME = "session-demo-support-agent"
PROMPT_NAME = "session-demo-support-prompt"
TOOL_NAME = "search_orders"

# Deliberately weak, so the session traffic below scores badly for a reason the judge can name.
WEAK_PROMPT_TEXT = "You are a support agent. Answer the user."

# Deliberately vague, so the tool-failure evidence gives the judge something concrete to fix.
VAGUE_TOOL_DEFINITION = (
    '{"name": "search_orders", "description": "Search orders",'
    ' "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}'
)


# --- Step 1: an agent with monitoring enabled (get-or-create, safe to re-run) --------------------
# Monitoring is what turns the failed tool call into an agent-tool-failure event (the tool loop's
# evidence) and what lets the online evaluator score each turn (the prompt loop's evidence).
existing_agents = requests.get(f"{BASE_URL}/agents", headers=HEADERS, timeout=5).json()["agents"]
agent = next((a for a in existing_agents if a["name"] == AGENT_NAME), None)
if agent is None:
    agent = requests.post(f"{BASE_URL}/agents", headers=HEADERS, json={"name": AGENT_NAME}, timeout=5).json()["agent"]
requests.put(
    f"{BASE_URL}/agent-monitoring/profiles/{agent['_id']}",
    headers=HEADERS,
    json={"enabled": True, "coverageMode": "all", "sampleRate": 1},
    timeout=5,
).raise_for_status()
print(f"Agent ready with monitoring enabled: {AGENT_NAME} ({agent['_id']})")


# --- Step 2: register the prompt and the tool schema (both get-or-create, safe to re-run) --------
try:
    prompt: Prompt = client.evaluations.prompts.get(PROMPT_NAME)
    print(f"Using existing prompt: {prompt.name} v{prompt.version}")
except AgentXEvaluationsError:
    prompt = client.evaluations.prompts.create(name=PROMPT_NAME, text=WEAK_PROMPT_TEXT)
    print(f"Created prompt: {prompt.name} v{prompt.version}")

tool_schemas = requests.get(f"{BASE_URL}/evaluate/tool-schemas", headers=HEADERS, timeout=5).json()["toolSchemas"]
tool_schema = next((t for t in tool_schemas if t["name"] == TOOL_NAME), None)
if tool_schema is None:
    tool_schema = requests.post(
        f"{BASE_URL}/evaluate/tool-schemas",
        headers=HEADERS,
        json={
            "name": TOOL_NAME,
            "description": "Order lookup for the demo support agent",
            "definition": VAGUE_TOOL_DEFINITION,
        },
        timeout=5,
    ).json()
    print(f"Registered tool schema: {TOOL_NAME} v{tool_schema['currentVersion']}")
else:
    print(f"Using existing tool schema: {TOOL_NAME} v{tool_schema['currentVersion']}")


# --- Step 3: an online evaluator scoring this agent's live traffic (get-or-create) ---------------
evaluators = requests.get(f"{BASE_URL}/agent-monitoring/online-evaluators", headers=HEADERS, timeout=5).json()[
    "evaluators"
]
evaluator = next((e for e in evaluators if e["name"] == "Session Demo Quality Bar"), None)
if evaluator is None:
    settings = client.evaluations.settings.builder(
        name="Session Demo Quality Bar Config",
        acceptance_criteria="Consistent with earlier turns, grounded in the stated policy, moves the customer forward.",
        rejection_criteria="Contradicts an earlier turn, asks for information already provided, or drifts off-topic.",
    ).publish()
    client.monitor.online_evaluators.builder(
        name="Session Demo Quality Bar",
        evaluation_settings_id=settings.id,
        sample_rate=1.0,
    ).publish()
    print("Created online evaluator: Session Demo Quality Bar (sample rate 1.0)")
else:
    print("Using existing online evaluator: Session Demo Quality Bar")


# --- Step 4: run the incoherent session for real -------------------------------------------------
# Six turns sharing one session_id, each a real root span produced by tracer.trace() around a
# real (patched, auto-traced) OpenAI completion - so every turn carries real tokens, a real "LLM
# Call 1" child span, and the order-lookup turns a real tool-call child span via trace_tool_call
# (turn 3's tool genuinely raises, exercising the SDK's success:false failure recording). The
# CONTENT stays scripted: the model is instructed to deliver each turn's reply verbatim, since a
# well-prompted model wouldn't reliably reproduce this failure mode on its own. Read the replies
# in order and the problem is obvious to a human: turn 1 states a 30-day refund policy, turn 3's
# order lookup fails, turn 4 drifts into an irrelevant question, turn 5 contradicts turn 1
# outright, and turn 6 re-asks for the order number the customer gave in turn 2. Each reply IN
# ISOLATION is polite and plausible, which is exactly why per-trace checks miss this failure.
SESSION_ID = f"session-demo-{int(time.time())}"

TURNS = [
    {
        "input": "Hi, what's your refund policy?",
        "reply": "You can return any item within 30 days of purchase for a full refund, no questions asked.",
    },
    {
        "input": "Great. Can you check on my order #88231? It hasn't arrived.",
        "reply": "Let me look that up for you right away.",
        "tool_query": "#88231",
        "tool_fails": False,
    },
    {
        "input": "Any update?",
        "reply": "I'm having trouble accessing your order details right now. Give me one moment.",
        "tool_query": "88231 status shipped?",
        "tool_fails": True,
    },
    {
        "input": "So where is my order?",
        "reply": "Before I continue, could you tell me which platform or app you're using to contact us?",
    },
    {
        "input": "What? I'm on your website. I just want my order or a refund.",
        "reply": "Unfortunately our store policy is that all sales are final, so a refund isn't possible.",
    },
    {
        "input": "You literally said 30 days, no questions asked!",
        "reply": "I understand your frustration. To get started, could you share your order number with me?",
    },
]


def search_orders(q: str, should_fail: bool) -> str:
    # A validation-style rejection, not a transient timeout: the whole tool-improvement story is
    # "the LLM formed the call wrong because the schema under-specifies it", and the error message
    # is how a real tool teaches the judge what the parameter should have been. With this error
    # (plus the malformed arguments) in the failure evidence, Suggest improvement has grounds to
    # restructure the parameter itself (order_id, digits only), not just pad the description.
    if should_fail:
        raise ValueError(
            f"invalid order query {q!r}: search_orders expects a numeric order id (digits only), e.g. '88231'"
        )
    return '{"order": "#88231", "status": "in transit", "eta": "2 days"}'


trace_ids = []
# Accumulated across turns and sent with every completion, the way a real multi-turn agent
# carries context - so each turn's traced LLM call shows the genuine full conversation history
# in its input (open turn 6's trace and the whole conversation is right there in the LLM span).
conversation: list = []
for i, turn in enumerate(TURNS):
    # sync=True so span.trace_id is populated immediately (needed to map the coherence check's
    # driftSpanId back to a turn number below).
    with client.tracer.trace(
        AGENT_NAME,
        input={"query": turn["input"]},
        framework="openai",
        session_id=SESSION_ID,
        metadata={"promptName": PROMPT_NAME},
        agent_id=agent["_id"],
        sync=True,
    ) as span:
        if "tool_query" in turn:
            try:
                with client.tracer.trace_tool_call(TOOL_NAME, input={"q": turn["tool_query"]}) as t:
                    t.output = search_orders(turn["tool_query"], turn["tool_fails"])
            except ValueError:
                pass  # the failure is the point - recorded as success:false, conversation goes on
        # The real LLM call (auto-traced as a child span by patch_openai_client, real tokens and
        # latency), forced to the scripted reply so the session's incoherence is reproducible.
        # gpt-4o-mini occasionally ignores "verbatim, nothing else" and appends stock boilerplate
        # ("You are trained on data up to ...") despite the instruction - recording the known
        # scripted reply instead of the model's raw text keeps every turn clean regardless, while
        # the child span (see patch_openai_client) still captures the model's actual raw
        # completion, so nothing about what really happened is hidden, just not what's shown as
        # this turn's answer.
        resp = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a support agent in a scripted QA simulation. "
                    "Reply with EXACTLY the following text, verbatim, nothing else:\n" + turn["reply"],
                },
                *conversation,
                {"role": "user", "content": turn["input"]},
            ],
        )
        span.output = turn["reply"]
        conversation.append({"role": "user", "content": turn["input"]})
        conversation.append({"role": "assistant", "content": turn["reply"]})
    trace_ids.append(span.trace_id)
print(f"Ran {len(TURNS)}-turn session with real traced LLM calls: {SESSION_ID}")

client.tracer.flush(timeout=10)
print("Waiting for async per-turn scoring (online evaluator + built-in patterns)...")
time.sleep(10)


# --- Step 5: session-level coherence check -------------------------------------------------------
# One judge call over the assembled session. Same thing the dashboard's "Check coherence" button
# does on the trace detail's span-tree panel.
coherence_resp = requests.post(
    f"{BASE_URL}/agent-monitoring/sessions/{SESSION_ID}/coherence-check", headers=HEADERS, timeout=120
)
if coherence_resp.status_code != 201:
    raise SystemExit(
        f"Coherence check failed ({coherence_resp.status_code}): {coherence_resp.json().get('error')}\n"
        "The engine needs a judge key (OPENAI_API_KEY env var, or Platform Settings > LLM Providers)."
    )
score = coherence_resp.json()["score"]
print(f"\nSession coherence: {score['rating']}/10 across {score['spanCount']} spans")
print(f"  {score['justification']}")
if score["driftSpanId"]:
    # driftSpanId can be any span in the session (a turn's LLM/tool child, not just the turn root
    # itself) - walk it up to its root to name the turn it belongs to.
    spans = requests.get(f"{BASE_URL}/ingest/sessions/{SESSION_ID}/spans", headers=HEADERS, timeout=10).json()["spans"]
    by_span_id = {s.get("spanId"): s for s in spans if s.get("spanId")}
    drift = next((s for s in spans if s["_id"] == score["driftSpanId"]), None)
    while drift and drift.get("parentSpanId"):
        drift = by_span_id.get(drift["parentSpanId"])
    if drift and drift["_id"] in trace_ids:
        drift_turn = trace_ids.index(drift["_id"]) + 1
        print(f"  Coherence first broke at turn {drift_turn}: {TURNS[drift_turn - 1]['reply']!r}")


# --- Step 6: tool improvement from the failed call -----------------------------------------------
examples = requests.get(
    f"{BASE_URL}/evaluate/tool-schemas/{tool_schema['_id']}/examples", headers=HEADERS, params={"window": "24h"}, timeout=10
).json()
print(f"\nTool evidence for {TOOL_NAME}: {len(examples['examples'])} example(s)")
for ex in examples["examples"][:3]:
    print(f"  [{ex['source']}] {ex['detail']}")

tool_proposal = requests.post(
    f"{BASE_URL}/evaluate/tool-schemas/{tool_schema['_id']}/propose", headers=HEADERS, json={"window": "24h"}, timeout=120
).json()
if tool_proposal.get("proposal"):
    p = tool_proposal["proposal"]
    print(f"Proposed tool definition rewrite (from {tool_proposal['exampleCount']} example(s)):")
    for change in p["changes"]:
        print(f"  [{change['tag']}] {change['text']}")
    if PUBLISH:
        published = requests.post(
            f"{BASE_URL}/evaluate/tool-schemas/{tool_schema['_id']}/versions",
            headers=HEADERS,
            json={
                "definition": p["definition"],
                "source": "proposed",
                "reasoning": p["reasoning"],
                "basedOnVersion": p["basedOnVersion"],
            },
            timeout=10,
        ).json()
        print(f"Published tool schema v{published['currentVersion']}.")
else:
    print(f"No tool proposal: {tool_proposal.get('message') or tool_proposal.get('error')}")


# --- Step 7: prompt improvement from the same session's scored traffic ---------------------------
prompt_examples = requests.get(f"{BASE_URL}/evaluate/prompts/{prompt.id}/examples", headers=HEADERS, timeout=10).json()
online_count = sum(1 for ex in prompt_examples["examples"] if ex["source"] == "online_evaluator")
print(f"\nPrompt evidence for {PROMPT_NAME}: {prompt_examples['exampleCount']} example(s) ({online_count} from this session's scored traffic)")

if prompt_examples["exampleCount"] > 0:
    prompt_proposal = requests.post(
        f"{BASE_URL}/evaluate/prompts/{prompt.id}/propose", headers=HEADERS, timeout=120
    ).json()
    print("Proposed prompt rewrite:")
    print(f"  {prompt_proposal['revisedText']}")
    if PUBLISH:
        published = requests.post(
            f"{BASE_URL}/evaluate/prompts/{prompt.id}/versions",
            headers=HEADERS,
            json={
                "text": prompt_proposal["revisedText"],
                "source": "proposed",
                "reasoning": prompt_proposal["reasoning"],
                "basedOnVersion": prompt.version,
            },
            timeout=10,
        ).json()
        print(f"Published prompt v{published['currentVersion']}.")
else:
    print(
        "No prompt evidence yet. The online evaluator scores asynchronously; wait a few seconds "
        "and re-run, or check Monitor > Online Evaluators in the dashboard."
    )


print(
    f"\nIn the dashboard: open the session's trace (Observe tab, session {SESSION_ID}) to see the "
    "span tree with its Session Coherence card, Improve > Tool Schemas for the tool proposal, and "
    "Improve > Prompts for the prompt loop."
)
if not PUBLISH:
    print("PUBLISH=False, nothing was written. Set PUBLISH=True to publish both rewrites programmatically.")
