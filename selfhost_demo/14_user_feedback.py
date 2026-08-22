"""
End-user feedback: the cheapest ground truth there is. Your app already has (or should have) a
thumbs-up/down button next to every agent response - this script is that button, in eight lines
of SDK.

What one client.feedback.report(trace_id, "down") call does on the engine:
  1. attaches the vote (and the user's own words) to the trace - visible as chips at the top of
     the trace dialog in Observe,
  2. raises a "Negative user feedback" signal in Monitor for triage - the user IS the detector,
     so there is no scorer to configure, no sampling, no judge call, and nothing to enable,
  3. feeds Judge Calibration as an outcome report, so AgentX's automated verdicts get measured
     against real human reactions (see 11_feedback_calibration_and_judge_tuning.py for that
     full loop),
  4. moves the Overview "Downvote rate" KPI - the share of votes in the window that were "down".

Feedback is deliberately NOT a scorer: scorers (Template/LLM Judge/Custom, the Scorers tab) are
judgments you opt into; feedback is what actually happened. That's also how Braintrust/LangSmith/
Langfuse model it - a human annotation attached to the trace, used to calibrate the scorers.

No LLM key needed: the "agent" here is simulated with plain traces (sync=True so each trace_id
is usable immediately - exactly what your app's vote handler needs to hold on to).
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


# --- Step 1: an agent answers three users ---------------------------------------------------
# In your app this is the traced agent call you already have; keep the trace_id next to the
# rendered response so the vote button knows what it's voting on.
EXCHANGES = [
    ("Where is my order #8812?", "It left our warehouse yesterday and arrives Thursday - tracking: 1Z999AA1."),
    ("How do I reset my password?", "Go to Settings > Security > Reset password; the email link is valid for 1 hour."),
    ("Cancel my subscription", "I understand you want changes! We truly value you as a customer and are always here to help with anything you need."),
]

trace_ids = []
for query, reply in EXCHANGES:
    with client.tracer.trace("feedback-demo-agent", input={"query": query}, sync=True) as span:
        span.output = reply
    trace_ids.append(span.trace_id)
    print(f"traced: {query!r} -> {span.trace_id}")


# --- Step 2: the users react ----------------------------------------------------------------
# Two got what they asked for; the third got warmth instead of a cancellation.
helpful_1, helpful_2, non_answer = trace_ids

client.feedback.report(helpful_1, "up", end_user_id="demo-user-1")
client.feedback.report(helpful_2, "up", end_user_id="demo-user-2")
downvote = client.feedback.report(
    non_answer,
    "down",
    comment="It never actually cancelled anything, just talked at me",
    end_user_id="demo-user-3",
)
print(f"\nvotes: 2 up, 1 down (downvote id {downvote['_id']})")


# --- Step 3: what the engine did with them --------------------------------------------------
votes = client.feedback.list(non_answer)
print(f"\nfeedback on the non-answer trace ({len(votes)} vote):")
for vote in votes:
    print(f"  {vote['rating']!r} from {vote.get('endUserId')}: {vote.get('comment')}")

signals = client.monitor.signals.list()
feedback_signals = [s for s in signals if s.pattern_key == "negative-feedback"]
print(f"\n'Negative user feedback' signals in Monitor: {len(feedback_signals)}")
for signal in feedback_signals:
    print(f"  [{signal.severity}] {signal.summary}")

kpis = client.monitor.kpis(window="24h")
rate = kpis.get("downvoteRate")
print(f"\nOverview downvote rate (24h): {rate if rate is None else f'{rate:.0%}'} (1 down / 3 votes)")

print(
    "\nOpen the dashboard: Monitor shows the downvote as a 'User feedback' signal, the trace"
    "\ndialog shows the votes as chips, and Overview's Downvote rate card is no longer '-'."
)
