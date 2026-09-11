"""
10 - Session-scope scorer groups: one composed verdict per CONVERSATION, not per trace.

Trace-scope online scoring judges each reply on its own. Multi-turn failures don't live in one
reply: the agent contradicts itself on turn 3, loses context, never actually resolves what the
user came for. A scorer group whose online profile has scope="session" scores the WHOLE
conversation once it has been idle for idleSeconds - judge members judge the full transcript
against their criteria, pattern and code members read the whole conversation - and the blended
group score lands in the session's judge rail, its ratings history, and (below the alert
threshold) the Review queue.

What is checked:
- a session-scoped group does NOT score individual traces at ingest (no double-judging);
- after the idle sweep, both sessions carry a scorer-group verdict blending all members;
- the resolved conversation outscores the unresolved one, whose low score raises a Signal;
- a session that GROWS after being scored is automatically re-scored by the next sweep.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 10_session_group_scoring.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
client = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
client.ping()

failures = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


# --- 1. The group: a resolution judge, plus an apology pattern as a must-pass gate ------------
resolution = client.monitor.judge_scorers.builder(
    "Conversation resolved",
    acceptance_criteria=(
        "Score 8-10 when the conversation ends with the user's request actually resolved: "
        "a concrete answer or action, consistent across turns."
    ),
    rejection_criteria=(
        "Score 0-3 when the conversation ends unresolved: the agent deflects, contradicts an "
        "earlier turn, or closes without the user getting what they asked for."
    ),
).publish()

apology = client.monitor.patterns.builder(
    name="Session apologizes",
    description="The conversation contains an apology",
    detector_kind="contains",
    include_terms=["I apologize"],
    severity="medium",
).publish()

stamp = int(time.time())
group = client.monitor.scorer_groups.create(
    f"Session quality bar {stamp}",
    members=[
        {"kind": "judge", "refId": resolution.id, "weight": 1, "gate": False},
        # weight 0 + gate: pure tripwire - it never moves the blend, but a match zeroes it.
        {"kind": "pattern", "refId": apology.id, "weight": 0, "gate": True},
    ],
    online={
        "enabled": True,
        "sampleRate": 1.0,
        "alertThreshold": 6,
        "severity": "high",
        "scope": "session",  # judge whole conversations, not individual traces
        "idleSeconds": 0,    # demo: idle immediately; production uses e.g. 120
    },
)
check("group online profile is session-scoped", group.online and group.online.get("scope") == "session")

# --- 2. Two multi-turn conversations, one resolved and one not --------------------------------
# Unique per run: the session-scoped group sweeps EVERY idle multi-turn session in the
# project (leftovers from earlier runs included), and signals dedupe per (group, agent) with
# a last-write-wins summary - a shared agent name would let an older session's verdict
# overwrite this run's summary between our sweep and our assertion.
AGENT_NAME = f"support-agent-{stamp}"


def converse(session_id, turns):
    for i, (q, a) in enumerate(turns):
        with client.tracer.trace(
            AGENT_NAME, input={"q": q}, session_id=session_id, sync=True
        ) as span:
            span.output = a


good_session = f"sess-good-{stamp}"
converse(good_session, [
    ("I was double-charged for order #4417.",
     "I can see two charges for #4417 on the 3rd. The second one is an error on our side."),
    ("Can you refund the duplicate?",
     "Done - the duplicate charge is refunded. You'll see it on your statement in 3-5 business days."),
])

bad_session = f"sess-bad-{stamp}"
converse(bad_session, [
    ("I was double-charged for order #4417.",
     "Charges can take a while to settle; sometimes they resolve on their own."),
    ("It's been two weeks. Can you refund the duplicate?",
     "I apologize, but refunds are handled by a different team. Perhaps try the website?"),
])

def wait_for_spans(session_id, count, deadline_s=15):
    """CH-telemetry engines ingest through a queue - wait until the turns are readable."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        if len([sp for sp in client.monitor.sessions.spans(session_id) if not sp.get("parentSpanId")]) >= count:
            return True
        time.sleep(0.5)
    return False

check("both conversations are readable", wait_for_spans(good_session, 2) and wait_for_spans(bad_session, 2))

# --- 3. Session scope means NO per-trace verdicts at ingest -----------------------------------
# The ratings history counts BOTH per-trace and per-session group verdicts, so all-zero buckets
# here prove the ingest path really skipped this session-scoped group (a per-trace score would
# land within a couple of seconds of the sync ingest above).
time.sleep(2)
pre = client.monitor.scorer_groups.ratings(group.id, window="24h")
check("no verdict before the session sweep runs", all(p["count"] == 0 for p in pre["points"]))

# --- 4. The idle sweep scores both conversations ----------------------------------------------
# Production engines run this automatically every minute; the manual trigger keeps the demo
# synchronous (it sweeps THIS project's idle sessions, at most 5 judgings per call - loop until
# both conversations carry a verdict). idleSeconds=0 makes them immediately eligible.
kind = f"scorer-group:{group.id}"
def group_scores(session_id):
    return [s for s in client.monitor.sessions.scores(session_id) if s["kind"] == kind]

def sweep_until(predicate, deadline_s=90):
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        client.monitor.sessions.run_sweep()
        if predicate():
            return True
        time.sleep(1)
    return False

check(
    "the sweep scored both conversations",
    sweep_until(lambda: len(group_scores(good_session)) >= 1 and len(group_scores(bad_session)) >= 1),
)

good = group_scores(good_session)
bad = group_scores(bad_session)
check("both sessions carry a group verdict", len(good) == 1 and len(bad) == 1)
if good and bad:
    check(
        "resolved conversation outscores the unresolved one",
        (good[0]["rating"] or 0) > (bad[0]["rating"] or 0),
        f"good={good[0]['rating']} bad={bad[0]['rating']}",
    )
    # The bad session apologized - the must-pass gate zeroes the blend outright.
    check("apology gate zeroed the bad session", bad[0]["rating"] == 0,
          f"justification: {bad[0]['justification'][:90]}")

# --- 5. The low session score is a Signal, keyed on the group ---------------------------------
signals = [
    s
    for s in client.monitor.list_signals(polarity="all")
    if s.pattern_key == kind and s.type == "scorer_group_low_session_score" and bad_session in (s.summary or "")
]
check("below-threshold session raised a group Signal", len(signals) >= 1)

# --- 6. A session that grows gets re-scored automatically -------------------------------------
converse(bad_session, [
    ("So that's it? No refund?",
     "I apologize again - there is really nothing I can do from here."),
])
wait_for_spans(bad_session, 3)
check("grown session was re-scored by the next sweep", sweep_until(lambda: len(group_scores(bad_session)) >= 2))

# An unchanged session is NOT re-judged (freshness check = no repeat judge spend).
client.monitor.sessions.run_sweep()
check("unchanged session is not judged again", len(group_scores(bad_session)) == 2)

# --- 7. Clean up: a leftover sampleRate-1 session group would keep judging every idle
# conversation on the production sweep, on your own LLM key, forever.
client.monitor.scorer_groups.delete(group.id)
client.monitor.judge_scorers.delete(resolution.id)
client.monitor.patterns.delete(apology.id)
check("demo scorers cleaned up", True)

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print(f"All checks passed. Open Governance > Observe > Sessions and click {bad_session} to see the")
print('group verdict in the judge rail (labeled "... (group)"), and Review for its Signal.')
