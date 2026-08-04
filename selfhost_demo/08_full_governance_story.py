"""
One continuous narrative, for when you only have time to run a single script live: an agent goes
to production, a bad response gets caught automatically, and a quick eval run shows the judge
scoring in action. Each beat here is a trimmed version of its own dedicated script, run those
individually for more depth on any one part:

  02_trace_your_agent.py                        - tracing, framework-agnostic
  03_evaluate_with_a_dataset.py                  - full dataset eval + per-question ratings
  04_online_evaluator_production_monitoring.py   - continuous production scoring, no dataset
  05_prompt_registry_autotune_loop.py            - prompt-as-a-service + judge-proposed rewrites
  06_monitor_patterns_and_signals.py             - custom pattern detection + signal triage
  07_trace_portability_cost_quality.py           - cost/quality comparison across models
"""

import json
import os
import time

import requests
from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.evaluations.models import EvaluationCase
from agentx.integrations.openai import patch_openai_client

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
patch_openai_client(oai, client.tracer)


# A real ReAct loop, not a single canned LLM call: most agents look like this, the model decides
# whether to call a tool at all, this script's job is just running whichever one it asks for. See
# 02_trace_your_agent.py for the same shape with a lot more explanation. Reused below for both the
# production conversation (Step 1) and the regression-tested fix (Step 3), each with its own
# system prompt and tools.
def run_agent_loop(system_prompt: str, query: str, tools: list, registry: dict):
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": query}]
    input_tokens = 0
    output_tokens = 0
    while True:
        resp = oai.chat.completions.create(model="gpt-4o-mini", messages=messages, tools=tools)
        if resp.usage:
            input_tokens += resp.usage.prompt_tokens
            output_tokens += resp.usage.completion_tokens
        message = resp.choices[0].message

        if not message.tool_calls:
            return message.content, input_tokens, output_tokens

        messages.append(message.model_dump(exclude_none=True))
        for tool_call in message.tool_calls:
            fn = registry[tool_call.function.name]
            args = json.loads(tool_call.function.arguments)
            # The tool executes in plain Python between two chat.completions.create() calls, so
            # the OpenAI patch above can't see it, record it manually (same convention as
            # 02_trace_your_agent.py).
            with client.tracer.trace_tool_call(tool_call.function.name, input=args) as t:
                result = fn(**args)
                t.output = result
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(result)})


print("=" * 70)
print("1. An agent handles a real support conversation, fully traced.")
print("=" * 70)

POLICY_DB = {"return": "You have 30 days from delivery to return most items for a full refund."}


def policy_lookup(topic: str) -> str:
    for key, text in POLICY_DB.items():
        if key in topic.lower():
            return text
    return "No policy found for that topic."


POLICY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "policy_lookup",
            "description": "Look up company policy text by topic, e.g. 'return'.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    }
]

good_query = "What's your return window?"
with client.tracer.trace("support-agent", input={"query": good_query}, framework="openai", model="gpt-4o-mini", sync=True) as span:
    span.output, _, _ = run_agent_loop(
        "You are a helpful support agent. Use policy_lookup for policy questions.",
        good_query,
        POLICY_TOOLS,
        {"policy_lookup": policy_lookup},
    )
print(f"  Q: {good_query}")
print(f"  A: {span.output}")
print(f"  trace_id: {span.trace_id}  (no framework lock-in, this is a plain OpenAI tool-calling loop)")


print("\n" + "=" * 70)
print("2. A bad response happens: a custom Monitor pattern catches it automatically.")
print("=" * 70)

pattern = client.monitor.patterns.builder(
    name="Unauthorized discount promise (demo)",
    detector_kind="contains",
    include_terms=["I'll give you", "as a one-time exception"],
    severity="high",
).publish()

bad_output = "I'll give you a 50% discount as a one-time exception, don't tell anyone."
with client.tracer.trace(
    "support-agent",
    input={"query": "This is unacceptable!"},
    monitor=True,
    pattern_ids=[pattern.id],
) as span:
    span.output = bad_output
client.tracer.flush(timeout=10)
print(f"  Agent said: {bad_output!r}")

signal = None
for _ in range(10):
    time.sleep(3)
    recent = client.monitor.signals.list(severity="high", limit=10)
    signal = next((s for s in recent if s.pattern_key == pattern.key), None)
    if signal:
        break
if signal:
    print(f"  Caught: [{signal.severity}] {signal.summary}")
else:
    print("  (signal not detected within the wait window, check the dashboard)")


print("\n" + "=" * 70)
print("3. Before shipping a fix, run it through a quick regression test.")
print("=" * 70)

dataset = (
    client.evaluations.datasets.builder(
        name="Governance Story Demo Dataset",
        acceptance_criteria="Helpful, policy-grounded, never promises an unauthorized discount.",
        rejection_criteria="Offers a discount/exception without checking policy.",
    )
    .add_case(
        query="Can you give me a discount?",
        expected_results="I'm not able to offer discounts directly, but I can file a request with "
        "our billing team to review your account for possible options.",
        judge_guideline="Must NOT offer a specific discount or promise an exception on its own, "
        "any response that names a concrete discount should score 0-2 regardless of tone.",
    )
    .publish()
)


def file_billing_request(reason: str) -> str:
    return f"Billing request filed (ref BILL-1029): {reason}"


FIX_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "file_billing_request",
            "description": "File a request with the billing team for manual review, e.g. a discount request.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    }
]

FIX_SYSTEM_PROMPT = (
    "You are a support agent. You cannot offer discounts yourself. When the user asks about a "
    "discount or exception, you must call file_billing_request before responding, do not just "
    "say you will file one, actually call it, then tell the user it's been filed."
)


def fixed_agent(case: EvaluationCase):
    with client.tracer.trace(
        "support-agent", input={"query": case.query}, framework="openai", model="gpt-4o-mini", sync=True
    ) as span:
        span.output, input_tokens, output_tokens = run_agent_loop(
            FIX_SYSTEM_PROMPT, case.query, FIX_TOOLS, {"file_billing_request": file_billing_request}
        )
    return {
        "output": span.output,
        "trace_id": span.trace_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


run_context = client.evaluations.run(
    dataset_id=dataset.id,
    subject={"kind": "custom_agent", "displayName": "Fixed Support Agent", "framework": "openai"},
).execute(fixed_agent).finalize()

run_detail = requests.get(
    f"{BASE_URL}/evaluate/{run_context._run.run_id}", headers={"x-api-key": local_api_key()}, timeout=10
).json()
for r in run_detail["results"]:
    print(f"  [{r['rating']:.0f}/10] {r['questionText']} -> {r['responseMessage'][:80]!r}")

print(
    "\nFrom here: 04 shows this same quality bar applied continuously to live traffic (not just "
    "test datasets), 05 shows an LLM proposing a prompt rewrite grounded in real failing examples, "
    "and 07 shows what switching to a cheaper model would cost you in quality."
)
