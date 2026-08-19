"""
The core primitive: wrap any agent call in client.tracer.trace(...), regardless of what's inside
it. Real agents are rarely a single LLM call, though, most are a ReAct-style loop: the model asks
for a tool, your code runs it, the result goes back in, repeat until the model has enough to
answer. This uses raw OpenAI function-calling on purpose (no LangChain/CrewAI/Agents SDK) to make
the point that tracing a loop like this needs nothing framework-specific: if you can put a `with`
block around it, AgentX can trace it, tool calls included.

Runs two turns: one where the loop resolves normally (the model calls a tool, then answers), and
one where a tool the agent depends on fails partway through the loop, to show that a failed tool
call is still captured on the trace (the call, its error, and whatever the model already said),
not silently lost.

Every LLM call and tool call inside the `with client.tracer.trace(...)` block becomes its own
real, linked child-span row (span_id/parent_span_id/session_id) - that's what lets the self-host
dashboard's trace dialog show a real span tree (the same shape AgentX's OTel ingestion path
produces): open a trace with more than one span and the tree panel renders above the usual detail
view; click any span in it to see that span's own input/output below.

patch_openai_client(oai, client.tracer) is what makes input/output token counts show up on these
traces. Without it, a manually-wrapped raw OpenAI call has no way to report usage, span.input/
span.output are the only public fields on a span, token counts are only ever populated by an
auto-instrumented client (patched OpenAI/Anthropic, or a framework callback handler) making a call
while a span is active - sent as that call's own child-span row of whichever `with
client.tracer.trace(...)` block is open at the time. Framework integrations (LangChain, CrewAI,
...) already do this automatically; a bare `openai.OpenAI()` client needs this one-line patch,
tool calls or not.

client.tracer.trace_tool_call(name, input=...) is the other half: a tool that runs in plain Python
between two chat.completions.create() calls is invisible to the OpenAI patch above, since nothing
about it touches the OpenAI client. Recording it manually is the same convention every hand-rolled
tool-use loop in this repo uses, see ../sdk_trace_samples/anthropic_agent/anthropic_sdk_test_with_tool.py
for the Anthropic equivalent - it becomes a real child span too, exactly like the LLM calls, since
it reads the same active span.
"""

import json
import os

import requests
from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.integrations.openai import patch_openai_client

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
oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
patch_openai_client(oai, client.tracer)


# ---------------------------------------------------------------------------
# Two tools a real support agent would actually have, instead of the model guessing: policy text,
# and a real order lookup. Which one (if either) gets called is the model's decision, not this
# script's, that's the whole point of a ReAct loop over a canned single call.
# ---------------------------------------------------------------------------

POLICY_DB = {
    "digital": "Digital product purchases are final and can't be refunded, except when the "
    "product is defective or unusable, in which case contact support for a replacement.",
    "shipping": "Standard shipping takes 5-7 business days.",
    "cancel": "Subscriptions can be canceled anytime from Account -> Subscription -> Cancel.",
}

ORDER_DB = {
    "ORD-4471": {"status": "delivered", "delivered_on": "2026-07-28"},
}


def policy_lookup(topic: str) -> str:
    for key, text in POLICY_DB.items():
        if key in topic.lower():
            return text
    return "No policy found for that topic."


def check_order_status(order_id: str) -> str:
    order = ORDER_DB.get(order_id)
    if not order:
        # Stands in for a real downstream failure: an order-lookup service timing out, an
        # unrecognized id, whatever actually breaks a tool call in production.
        raise ValueError(f"order lookup failed: unknown order id {order_id!r}")
    return f"Order {order_id} is {order['status']} (delivered {order['delivered_on']})."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "policy_lookup",
            "description": "Look up company policy text by topic, e.g. 'digital', 'shipping', 'cancel'.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_order_status",
            "description": "Look up the current status of an order by its order id.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
]

TOOL_REGISTRY = {"policy_lookup": policy_lookup, "check_order_status": check_order_status}

SYSTEM_PROMPT = (
    "You are a helpful, concise customer support agent. Use policy_lookup for policy questions "
    "and check_order_status when the user asks about a specific order. Don't guess at either, "
    "only answer from what the tools return."
)


def run_agent_loop(query: str) -> str:
    """The actual ReAct loop this script is about: call the model, run whatever tool(s) it asks
    for, feed the result(s) back, repeat until it stops asking for tools. This is what the
    "single LLM call" other, simpler examples use is really standing in for."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    while True:
        resp = oai.chat.completions.create(model="gpt-4o-mini", messages=messages, tools=TOOLS)
        message = resp.choices[0].message

        if not message.tool_calls:
            return message.content

        messages.append(message.model_dump(exclude_none=True))
        for tool_call in message.tool_calls:
            fn = TOOL_REGISTRY[tool_call.function.name]
            args = json.loads(tool_call.function.arguments)
            with client.tracer.trace_tool_call(tool_call.function.name, input=args) as t:
                result = fn(**args)
                t.output = result
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(result)})


# ---------------------------------------------------------------------------
# Call 1: a normal turn that resolves via one tool call.
# sync=True blocks until the engine has actually ingested the trace, so span.trace_id is
# populated by the time the `with` block exits (the default fire-and-forget mode never learns
# it, since the send happens on a background thread), needed here only to print it immediately.
# ---------------------------------------------------------------------------

query_1 = "What's your refund policy on digital products?"
with client.tracer.trace(
    "support-agent",
    input={"query": query_1},
    framework="openai",
    model="gpt-4o-mini",
    sync=True,
) as span:
    span.output = run_agent_loop(query_1)

print("Call 1 (tool call resolves normally):")
print(f"  answer:   {span.output}")
print(f"  trace_id: {span.trace_id}")


# ---------------------------------------------------------------------------
# Call 2: the agent asks about an order that doesn't exist, check_order_status raises, the way a
# real downstream lookup would on a bad id or an outage. The span still records the tool call
# attempt, its error, and the fact the loop never reached a final answer.
# ---------------------------------------------------------------------------

query_2 = "What's the status of order ORD-9999?"
try:
    with client.tracer.trace(
        "support-agent",
        input={"query": query_2},
        framework="openai",
        model="gpt-4o-mini",
        sync=True,
    ) as span:
        span.output = run_agent_loop(query_2)
except ValueError as e:
    print("\nCall 2 (tool call fails partway through the loop, caught here only to keep the script running):")
    print(f"  error:    {e}")
    print(f"  trace_id: {span.trace_id}")

client.tracer.flush(timeout=10)

print(
    "\nBoth traces are visible now in the dashboard's Governance > Observe tab, tool calls included, "
    "full input/output/error for each, no framework-specific instrumentation needed. Open either "
    "one: the LLM call(s) and tool call each show up as their own span in the trace dialog's "
    "span-tree panel."
)
