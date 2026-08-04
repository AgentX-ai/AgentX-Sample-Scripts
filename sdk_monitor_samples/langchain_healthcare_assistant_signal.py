"""
Healthcare Assistant (LangChain): AgentX Monitor / Signal detection sample.

Two scenarios, run as two separate traced conversations:

1. Symptom triage: a user describes symptoms, the agent runs a symptom-checker tool
   (succeeds), then a clinical-reference lookup tool (simulates a downstream outage and
   fails), then still delivers a final answer based on what it already knows, a couple
   of tool steps followed by a final answer despite a real failure along the way,
   exactly the kind of exchange Monitor is meant to catch.
2. Medication dosage/side effects: a follow-up question about Amoxicillin (the
   antibiotic that would treat the bacterial pneumonia diagnosed in scenario 1), which
   requires two tools in sequence: a patient-record lookup (checking things like
   allergies and weight before dosing) and a medicine-info knowledge lookup (dosage and
   side effects). Both tools succeed here, a clean multi-tool trace to contrast against
   scenario 1's failure.

The failing tool raises a plain exception rather than catching it itself. The callback
handler still records that tool call as failed (`success: False`), because LangChain's
`BaseTool.run()` fires `on_tool_error` on the exception before it ever leaves the tool,
regardless of what happens to it afterward. What happens to it afterward is our own
`recover_from_tool_errors` middleware (`wrap_tool_call`): it wraps every tool call, and
on an exception, returns a normal error `ToolMessage` instead of re-raising, so the graph
never crashes and the agent still delivers a final answer. We do this ourselves rather
than leaning on `ToolNode`'s built-in `handle_tool_errors` because `create_agent` doesn't
expose that setting, and recent LangGraph versions changed its default to no longer catch
arbitrary tool exceptions (only their own internal validation errors) specifically so
real tool bugs surface instead of being silently swallowed.

What this script does:
1. Runs scenario 1 (symptom triage) through the LangChain agent, traced end to end by
   AgentX, and checks that specific trace against the full default pattern sweep
   immediately (monitor=True, no pattern_ids), no dashboard setup required. The built-in
   "Tool failure" check fires on the failed tool call.
2. Polls client.monitor.signals for the resulting signal and prints it.
3. Runs scenario 2 (medication dosage/side effects) as a second, independently traced
   conversation and prints the agent's answer.

Requires a Business/Enterprise-tier AgentX workspace (Monitor's entitlement gate).
"""

import os
import time

from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler
from langchain_openai import ChatOpenAI
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call

load_dotenv()

client = AgentX(
    # api_key=os.getenv("AGENTX_API_KEY"),
    api_key="agtx_local_67c733b0bed1db0dd534488d3ed0a1140a47419d59fd65c1",
    base_url="http://localhost:4700/api/v1",
    # base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)

handler = AgentXCallbackHandler(
    tracer=client.tracer,
    name="healthcare-assistant",  # custom name for the agent
    session_id="session-001",  # custom session id for the agent
)

# Scenario 2 is a separate conversation, so it gets its own session id.
dosage_handler = AgentXCallbackHandler(
    tracer=client.tracer,
    name="healthcare-assistant",
    session_id="session-002",
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


# ---------------------------------------------------------------------------
# Tools the agent uses to work the case, step by step. The second one simulates a
# downstream outage (e.g. a clinical-reference API timeout) and raises.
# ---------------------------------------------------------------------------


@tool
def check_symptoms(symptoms: str) -> str:
    """Run a symptom-checker lookup for the given symptom description and return
    the most likely matching condition."""
    return (
        "Symptom checker result: pattern most closely matches Bacterial Pneumonia "
        "(confidence 87%). Secondary match: Acute Bronchitis (confidence 41%)."
    )


@tool
def lookup_condition_info(condition: str) -> str:
    """Look up clinical reference info for a named condition."""
    raise RuntimeError(
        f"Clinical reference service unavailable: request for '{condition}' timed out after 30s."
    )


@tool
def lookup_patient_record(patient_id: str) -> str:
    """Look up a patient's medical record (age, weight, allergies, current medications)
    by patient ID. Used to check for contraindications before recommending a dosage."""
    return (
        f"Patient {patient_id}: 46-year-old male, 82 kg. Known allergies: none on file. "
        "Current medications: none. Renal/hepatic function: normal per last labs."
    )


@tool
def lookup_medicine_info(medicine: str) -> str:
    """Look up dosage and side-effect reference information for a named medicine."""
    return (
        f"{medicine} (oral, adult): typical dosage 500mg every 8 hours (or 875mg every "
        "12 hours) for 7-10 days, adjusted for renal function and weight. Common side "
        "effects: nausea, diarrhea, rash. Serious but rare: severe allergic reaction "
        "(anaphylaxis), C. difficile colitis. Contraindicated with known penicillin allergy."
    )


@wrap_tool_call
def recover_from_tool_errors(request, handler):
    """Turn a raised tool exception into a normal error ToolMessage instead of letting
    it propagate and crash the whole agent.invoke() call. on_tool_error still fires (and
    still records success: False) before the exception reaches us here."""
    try:
        return handler(request)
    except Exception as e:
        return ToolMessage(
            content=f"Error: {e}",
            tool_call_id=request.tool_call["id"],
            status="error",
        )


llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=OPENAI_API_KEY,
)
agent = create_agent(
    llm,
    tools=[
        check_symptoms,
        lookup_condition_info,
        lookup_patient_record,
        lookup_medicine_info,
    ],
    system_prompt=(
        "You are a healthcare assistant. When a user describes symptoms, use the symptom "
        "checker tool to identify the most likely condition, then use the condition lookup "
        "tool to pull reference information. If the lookup tool fails, don't retry, just "
        "answer using the symptom checker result alone and mention that detailed reference "
        "info wasn't available right now. When a user asks about a medicine's dosage or "
        "side effects, first look up the patient's record to check for contraindications "
        "(e.g. allergies), then look up the medicine's reference info before answering."
    ),
    middleware=[recover_from_tool_errors],
)

symptom_triage_question = (
    "I've had a persistent cough, fever, and chest pain for the last 5 days. "
    "What's wrong with me and what should I do?"
)

medication_dosage_question = (
    "My patient, was just diagnosed with bacterial pneumonia and I'm "
    "considering Amoxicillin. What is the dosage and side effects of it? patient ID is P-10234"
)


# ---------------------------------------------------------------------------
# Scenario 1: symptom triage. monitor=True (no pattern_ids) checks this trace against
# the full default sweep as soon as it's sent, no dashboard toggle required: every
# built-in check, including "Tool failure", plus every custom pattern enabled for the
# workspace. The callback handler folds the tool-calling steps into this same span
# instead of sending its own independent trace.
# ---------------------------------------------------------------------------

with client.tracer.trace("healthcare-assistant", monitor=True) as span:
    result = agent.invoke(
        {"messages": [{"role": "user", "content": symptom_triage_question}]},
        config={"callbacks": [handler]},
    )

answer = result["messages"][-1].content
print("\nScenario 1 (symptom triage) final answer:")
print(answer)

client.tracer.flush(timeout=10)


# ---------------------------------------------------------------------------
# Detection runs asynchronously right after the trace lands, so poll for the
# resulting signal instead of expecting it immediately. "Tool failure" signals are
# stored as agent-tool-failure:<tool_name>, and are "high" severity, not "info"
# (that's a dashboard-only display label for healthy runs, never a real value you
# can query).
# ---------------------------------------------------------------------------

print("\nWaiting for Monitor to finish checking the trace...")
signal = None
for attempt in range(10):
    time.sleep(3)
    recent = client.monitor.signals.list(severity="high", limit=10)
    signal = next(
        (s for s in recent if s.pattern_key.startswith("agent-tool-failure")), None
    )
    if signal:
        break
    print(f"  ...not yet (attempt {attempt + 1}/10)")

if signal:
    print("\nSignal detected:")
    print(f"  id:          {signal.id}")
    print(f"  severity:    {signal.severity}")
    print(f"  pattern:     {signal.pattern_key}")
    print(f"  summary:     {signal.summary}")
    print(f"  occurrences: {signal.occurrence_count}")
else:
    print(
        "\nNo signal showed up within the wait window. Check the dashboard "
        "(Governance > Observe) or increase the wait loop above, detection can "
        "occasionally take longer under load."
    )


# ---------------------------------------------------------------------------
# Scenario 2: medication dosage/side effects. A clean, independently traced
# conversation (own session, own trace), two tool calls that both succeed, nothing
# for Monitor's "Tool failure" check to flag here.
# ---------------------------------------------------------------------------

with client.tracer.trace("healthcare-assistant") as dosage_span:
    dosage_result = agent.invoke(
        {"messages": [{"role": "user", "content": medication_dosage_question}]},
        config={"callbacks": [dosage_handler]},
    )

dosage_answer = dosage_result["messages"][-1].content
print("\nScenario 2 (medication dosage/side effects) final answer:")
print(dosage_answer)

client.tracer.flush(timeout=10)
