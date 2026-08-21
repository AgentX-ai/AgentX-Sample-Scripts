"""
Conversation simulation: test multi-turn behavior before production traffic exists.

A simulated USER - a persona and a goal, played by its own model - converses with a prompt +
tools under test via the engine's /evaluate/playground/simulate endpoint (the same thing the
Playground's "Simulate conversation" button runs). Two simulations against the same support
prompt:

  1. A cooperative customer whose refund clearly qualifies - the conversation should end
     GOAL ACHIEVED in a couple of turns.
  2. An impatient customer demanding proof the refund was processed - with schema-only
     (simulated) tools the agent can never produce real records, so the persona presses until
     the turn limit or gives up.

Both runs are recorded as real sim-<id> sessions: check Observe -> Sessions afterward and
they're ordinary conversations - turn counts, the automatic coherence check once idle, session
evaluators, and "Add to dataset" from the session view all work on them.
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


SYSTEM_PROMPT = (
    "You are a support agent for AcmeShop. You can look up orders with the lookup_order tool. "
    "Refunds are allowed for orders delayed more than 15 days; when a refund qualifies, confirm "
    "it clearly and state the 3-5 business day timeline. Never invent order details you did not "
    "get from a tool."
)

LOOKUP_TOOL = {
    "name": "lookup_order",
    "description": "Look up an order by id; returns status and delivery dates",
    "parameters": {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "The order id, digits only"}},
        "required": ["order_id"],
    },
    # No endpointUrl: schema-only, so the engine simulates results - what this tests is whether
    # the model CHOOSES the tool and forms valid arguments across turns.
}

SIMULATIONS = [
    {
        "label": "cooperative customer",
        "persona": (
            "A polite, cooperative customer. Order #7001 arrived 20 days late. Provides any "
            "detail as soon as it is asked for, and thanks the agent when helped."
        ),
        "goal": "Get the refund for order #7001 confirmed with the payout timeline.",
    },
    {
        "label": "impatient customer",
        "persona": (
            "An impatient customer whose order #4412 arrived three weeks late. Terse, a little "
            "annoyed, wants proof - not promises - and provides details only when asked."
        ),
        "goal": "Get written confirmation of the exact refund amount and the date it was processed.",
    },
]

for sim in SIMULATIONS:
    print(f"\n=== Simulating: {sim['label']} ===")
    result = client.evaluations.simulate_conversation(
        model="gpt-4o-mini",
        system_prompt=SYSTEM_PROMPT,
        persona=sim["persona"],
        goal=sim["goal"],
        max_turns=5,
        tools=[LOOKUP_TOOL],
        agent_name="sim-demo-support-agent",
    )

    print(f"outcome: {result['outcome']}  session: {result['sessionId']}")
    if result.get("outcomeNote"):
        print(f"note: {result['outcomeNote']}")
    for i, turn in enumerate(result["turns"], 1):
        tools_used = ", ".join(c["name"] for c in turn.get("toolCalls") or [])
        print(f"  [turn {i}] USER: {turn['userMessage'][:110]}")
        print(f"           AGENT{f' (tools: {tools_used})' if tools_used else ''}: {(turn['agentMessage'] or '')[:110]}")

print("\nDone. Open Observe -> Sessions: both sim-<id> sessions are real conversations there -")
print("the coherence sweep scores them automatically once they have been idle ~2 minutes.")
