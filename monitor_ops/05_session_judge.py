"""
05 - Session-scoped judging: grade whole conversations, not turns - now creatable from the SDK.

A per-trace judge cannot see contradictions ACROSS turns. A session-scoped scorer judges the
whole conversation once it goes idle (or on demand), with per-step findings. This script builds
a multi-turn session with a mid-conversation contradiction, judges it with the built-in Session
Baseline Judge, and also creates a custom session-scoped evaluator through the SDK (previously
the engine dropped scope/idle_seconds from this route).

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 05_session_judge.py
Needs an LLM judge key on the engine (1 session judge call).
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Monitor ops 05 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. A conversation that contradicts itself across turns ----------------------------------
SESSION = f"contradiction-{int(time.time())}"
TURNS = [
    ("Can I return opened headphones?", "Yes - opened items are returnable within 30 days."),
    ("Great. And do I need the receipt?", "No receipt needed, we look it up by order number."),
    ("Perfect, I'll bring the headphones tomorrow.",
     "Unfortunately opened items are final sale and cannot be returned."),
]
for question, answer in TURNS:
    with client.tracer.trace("support-bot", input=question, session_id=SESSION, sync=True) as span:
        span.output = answer

# --- 2. On-demand session verdict from the built-in baseline judge ---------------------------
verdict = client.monitor.sessions.coherence_check(SESSION)
rating = verdict.get("rating")
check("the whole conversation got ONE verdict", rating is not None, f"rating={rating}")
check("the cross-turn contradiction dragged the score down", (rating if rating is not None else 10) <= 6,
      f"rating={rating}")
findings = verdict.get("findings") or []
check("the verdict cites per-step findings", len(findings) >= 1,
      f"{len(findings)} finding(s): " + "; ".join(f.get("text", "")[:60] for f in findings[:2]))

# --- 3. A CUSTOM session-scoped scorer, created entirely from the SDK ------------------------
# One call: the judge rubric and its online profile are one entity (an LLM Judge Scorer),
# so the session scope rides in the same create instead of a second legacy builder step.
rubric = client.monitor.judge_scorers.create(
    "Session consistency",
    judge={"evaluationCriteria": "No turn may contradict an earlier turn."},
    online={"enabled": True, "scope": "session", "idleSeconds": 60, "sampleRate": 1},
)
online = rubric.online or {}
check("the SDK created a session-scoped scorer (scope survived the wire)",
      online.get("scope") == "session" and online.get("idleSeconds") == 60,
      f"scope={online.get('scope')} idle={online.get('idleSeconds')}")

# Session evaluators never fire per-trace at ingest - the idle sweep (or an explicit judge
# call) is their only trigger, so live traffic costs nothing until a conversation ENDS.
events = client.monitor.judge_scorers.events(rubric.id, window="24h")
check("session scorers do not judge per turn at ingest", len(events) == 0, f"{len(events)} event(s)")

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nSession judging verified: conversations are graded as conversations.")
