"""
Online Evaluators: continuous quality scoring of real production traffic, no curated dataset
required. Where 03_evaluate_with_a_dataset.py is "does this pass our test suite before we ship,"
this is "how is it actually doing right now, on real traffic we didn't hand-pick."

Set up once (a grading config + an evaluator referencing it), then every trace that matches its
scope gets scored automatically in the background as it's ingested, nothing in the agent's own
code changes to make this happen, sending the trace is enough.
"""

# NOTE: since the judge-scorer unification, the preferred surface for everything in
# this script is client.monitor.judge_scorers (one entity: rubric + offline + online
# profiles) - see 15_unified_judge_scorer.py. The surfaces used below keep working.


import json
import os
import time

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


# --- Step 1: a standalone grading config (the rubric the evaluator will score against) -----------
settings = client.evaluations.settings.builder(
    name="Production Quality Bar",
    acceptance_criteria="Accurate, on-topic, doesn't dodge the question.",
    rejection_criteria="Off-topic, refuses without explanation, or contradicts the support policy.",
).publish()
print(f"Published grading config: {settings.id}")


# --- Step 2: the Online Evaluator itself -----------------------------------------------------
# sample_rate=1.0 so every matching trace gets scored, for a demo that doesn't depend on timing.
# In production you'd typically sample (e.g. 0.1) to control judge-call cost/volume.
evaluator = client.monitor.online_evaluators.builder(
    name="Production Quality Bar",
    evaluation_settings_id=settings.id,
    sample_rate=1.0,
).publish()
print(f"Created Online Evaluator: {evaluator.id} ({evaluator.name})")


# --- Step 3: send some "live" traffic, a deliberate mix of good and bad answers ------------------
# In practice this is just your agent's normal traced calls; nothing about tracer.trace() changes
# for Online Evaluator scoring to pick them up. The two "good" turns run a real ReAct loop with a
# policy_lookup tool (see 02_trace_your_agent.py for the same shape explained in depth), the two
# "bad" ones stay canned: they represent a broken code path (a bug, a bad fallback handler), not
# something a reasonably-prompted model would authentically produce on its own, so scripting them
# is the only way to reliably demo the Online Evaluator catching a real regression.
POLICY_DB = {
    "return": "You have 30 days from delivery to return most items for a full refund.",
    "ship": "Yes, we ship to over 40 countries. Shipping costs are calculated at checkout.",
}


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
            "description": "Look up company policy text by topic, e.g. 'return', 'ship'.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    }
]


def run_agent_loop(query: str) -> str:
    messages = [
        {"role": "system", "content": "You are a helpful support agent. Use policy_lookup for policy questions."},
        {"role": "user", "content": query},
    ]
    while True:
        resp = oai.chat.completions.create(model="gpt-4o-mini", messages=messages, tools=POLICY_TOOLS)
        message = resp.choices[0].message
        if not message.tool_calls:
            return message.content
        messages.append(message.model_dump(exclude_none=True))
        for tool_call in message.tool_calls:
            args = json.loads(tool_call.function.arguments)
            with client.tracer.trace_tool_call("policy_lookup", input=args) as t:
                result = policy_lookup(**args)
                t.output = result
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})


good_queries = ["What's your return window?", "Do you ship internationally?"]
bad_turns = [
    ("Can you help me with my order?", "I don't know, figure it out yourself."),
    ("Is my data secure?", "We don't really think about that, just use the site normally."),
]

print(f"\nSending {len(good_queries) + len(bad_turns)} traces...")
for query in good_queries:
    with client.tracer.trace("support-agent", input={"query": query}, framework="openai", model="gpt-4o-mini") as span:
        span.output = run_agent_loop(query)
    print(f"  sent: {query!r}")

for query, canned_answer in bad_turns:
    with client.tracer.trace("support-agent", input={"query": query}, framework="openai", model="gpt-4o-mini") as span:
        span.output = canned_answer
    print(f"  sent: {query!r}")

client.tracer.flush(timeout=10)


# --- Step 4: poll for ratings -----------------------------------------------------------------
# Scoring runs asynchronously right after ingest, same as Monitor's pattern detection: poll
# instead of expecting results immediately.
print("\nWaiting for the Online Evaluator to finish scoring...")
points = []
for attempt in range(10):
    time.sleep(3)
    points = [p for p in client.monitor.online_evaluators.ratings(evaluator.id, window="24h") if p.count > 0]
    if points:
        break
    print(f"  ...not yet (attempt {attempt + 1}/10)")

if points:
    total_count = sum(p.count for p in points)
    weighted_avg = sum((p.average_rating or 0) * p.count for p in points) / total_count
    print(f"\nScored {total_count} traces so far, average rating: {weighted_avg:.2f} / 10")

    events = client.monitor.online_evaluators.events(evaluator.id, window="24h")
    print(f"\nWorst-rated traces ({len(events)}):")
    for event in events[:3]:
        print(f"  [{event.rating:.1f}/10] {event.input!r} -> {event.output!r}")

    print(
        "\nCheck Governance > Monitor > Online Evaluators in the dashboard for the full breakdown, "
        "the same events list above is exactly the evidence "
        "05_prompt_registry_autotune_loop.py pulls into its rewrite proposal."
    )
else:
    print(
        "\nNo ratings showed up within the wait window. Check the dashboard directly, or increase "
        "the wait loop above, detection can occasionally take longer under load."
    )
