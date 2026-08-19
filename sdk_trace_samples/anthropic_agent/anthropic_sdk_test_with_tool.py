import os

import requests
from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.anthropic import patch_anthropic_client
import anthropic

load_dotenv()

# No workspace_id, the API key alone selects the project. BASE_URL defaults to the local engine;
# the key itself is fetched from the unauthenticated bootstrap endpoint the same way the dashboard
# does on load, so nothing needs to be hand-copied into .env for this to run.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    key = os.getenv("AGENTX_API_KEY")
    if not key:
        raise SystemExit(
            "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup."
        )
    return key


agentx_client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

patch_anthropic_client(
    client,
    tracer=agentx_client.tracer,
    name="claude-support-agent-tool",
    metadata={"env": "production"},
    session_id="session-xyz-789",
)
# name/metadata/session_id above are only used for messages.create() calls
# made outside an active `with tracer.trace(...)` span - the loop below runs
# inside one, so its own name/metadata/session_id take over instead.


def policy_lookup(topic: str) -> str:
    """Look up a company policy by topic."""
    db = {
        "cancel": "Go to Account → Subscription → Cancel.",
        "trial": "14-day free trial, no credit card required.",
        "refund": "Full refund within 30 days.",
    }
    for key, val in db.items():
        if key in topic.lower():
            return val
    return "No policy found."


tools = [
    {
        "name": "policy_lookup",
        "description": "Look up a company policy by topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The policy topic to look up, e.g. 'cancel', 'trial', 'refund'.",
                }
            },
            "required": ["topic"],
        },
    }
]

# Dispatch table so the loop below can call whichever tool the model
# requests by name instead of hardcoding a single tool.
TOOL_REGISTRY = {
    "policy_lookup": policy_lookup,
}

MODEL = "claude-haiku-4-5-20251001"

question = "How do I cancel my subscription?"
messages = [{"role": "user", "content": question}]

# Wrap the whole agentic loop in one span. Every messages.create() call made
# while this span is active is aggregated as its own "LLM Call N" step on a
# single trace (in call order, interleaved with tool calls), instead of each
# call producing its own separate trace - see Tracer.current_span.
with agentx_client.tracer.trace(
    "claude-support-agent-tool",
    framework="anthropic",
    metadata={"env": "production"},
    session_id="session-xyz-789",
) as span:
    span.input = question

    # Agentic tool-use loop: keep calling the model until it stops requesting tools.
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system="You are a helpful support agent. Use the policy_lookup tool to answer policy questions.",
            tools=tools,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            fn = TOOL_REGISTRY.get(block.name)

            # The tool executes in plain Python between two messages.create()
            # calls, so the Anthropic patch can't see it - record it
            # manually, by its real name, so any tool the model calls shows
            # up in the trace.
            with agentx_client.tracer.trace_tool_call(
                block.name, input=block.input
            ) as t:
                result = fn(**block.input) if fn else f"Unknown tool: {block.name}"
                t.output = result

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                    "is_error": fn is None,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    # Final assistant text becomes the trace's output.
    for block in response.content:
        if block.type == "text":
            span.output = block.text
            print(block.text)

agentx_client.tracer.flush(timeout=10)
