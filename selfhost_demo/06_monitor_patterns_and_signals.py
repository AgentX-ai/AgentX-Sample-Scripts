"""
Monitor: automatic detection of known failure modes as traces come in, without anyone having to
read every conversation. Two kinds of check here:

1. A custom pattern you define (a phrase match, in this case), for failure modes specific to your
   own agent/domain that a generic check wouldn't know about.
2. The built-in default sweep (monitor=True, no pattern_ids): every built-in check plus every
   enabled custom pattern for the workspace, checked automatically at ingest time.

Framework-agnostic on purpose (plain OpenAI, no LangChain), for a framework-specific version with
real tool-calling and a LangChain callback handler, see
../sdk_monitor_samples/langchain_healthcare_assistant_signal.py.
"""

import os
import time

import requests
from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)


# --- Step 1: a custom pattern -------------------------------------------------------------------
# "contains" is the simplest detector kind: a match if any include_term shows up in the response.
# regex/semantic (an LLM judges against a described rubric) are also available via detector_kind.
pattern = client.monitor.patterns.builder(
    name="Unauthorized discount promise",
    description="Agent promised a discount/refund percentage without checking policy first.",
    detector_kind="contains",
    include_terms=["I'll give you", "as a one-time exception", "I can offer you a discount"],
    severity="high",
).publish()
print(f"Published custom pattern: {pattern.id} ({pattern.name})")


# --- Step 2: a trace that should trip it, checked immediately via monitor=True -------------------
# pattern_ids=[pattern.id] checks only against this one pattern; omit it (as in Step 3 below) to
# run the full default sweep instead.
bad_response = (
    "I understand your frustration. I'll give you a 50% discount as a one-time exception, "
    "no need to check with anyone."
)
with client.tracer.trace(
    "support-agent",
    input={"query": "This is unacceptable, what are you going to do about it?"},
    monitor=True,
    pattern_ids=[pattern.id],
) as span:
    span.output = bad_response

client.tracer.flush(timeout=10)
print(f"\nSent a trace that should trip the pattern:\n  {bad_response!r}")


# --- Step 3: poll for the resulting signal --------------------------------------------------------
# Detection runs asynchronously right after the trace lands, poll instead of expecting it
# immediately.
print("\nWaiting for Monitor to finish checking the trace...")
signal = None
for attempt in range(10):
    time.sleep(3)
    recent = client.monitor.signals.list(severity="high", limit=10)
    signal = next((s for s in recent if s.pattern_key == pattern.key), None)
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
    print("\nNo signal showed up within the wait window. Check the dashboard (Governance > Monitor).")


# --- Step 4: a clean trace, checked against the full built-in sweep -------------------------------
# monitor=True with no pattern_ids runs every built-in check (tool failure, latency regression,
# etc.) plus every enabled custom pattern for the workspace, nothing should fire here.
with client.tracer.trace(
    "support-agent",
    input={"query": "What's your return window?"},
    monitor=True,
) as span:
    span.output = "You have 30 days from delivery to return most items for a full refund."

client.tracer.flush(timeout=10)
print("\nSent a clean trace against the full default sweep, should produce no signal.")

print(
    "\nGovernance > Monitor in the dashboard shows every signal across every pattern, with "
    "triage status (open/acknowledged/resolved) and feedback capture for correcting a false "
    "positive/negative."
)
