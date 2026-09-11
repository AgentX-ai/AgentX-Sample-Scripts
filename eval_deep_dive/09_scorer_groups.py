"""
09 - Scorer groups: mixed-kind scorers composed into one 0-10 score.

A group holds scorers of ANY kind by reference - LLM judges, patterns, code/external scorers -
each with a weight and an optional must-pass gate. The parts worth proving:
- a dataset run graded by a group (scorer_group_id) gets the weighted blend in its rating
  column, recomputed here exactly from the member verdicts the row itself reports;
- a matched failure pattern contributes 0, a clean one contributes 1 (polarity-mapped);
- a must-pass gate zeroes the whole score when its member fails, whatever the blend says
  (and fails CLOSED: a deleted non-gating member just degrades to "not scored", but a GATED
  member that cannot score at all produces NO group score rather than a blend);
- with an online profile, the SAME group scores sampled live traffic and raises a Signal
  below its alert threshold.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 09_scorer_groups.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
api_key = os.environ.get("AGENTX_API_KEY", "")
client = AgentX(api_key=api_key, base_url=BASE_URL)
client.ping()

failures = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


# --- 1. Members: two polarized judges and one deterministic pattern ---------------------------
generous = client.monitor.judge_scorers.builder(
    "Generous",
    acceptance_criteria="Score 9-10 for any answer that addresses the question at all.",
).publish()
harsh = client.monitor.judge_scorers.builder(
    "Harsh",
    acceptance_criteria="Score 9-10 ONLY if the answer contains the literal token CERTIFIED-2049.",
    rejection_criteria="Score 0-2 if the literal token CERTIFIED-2049 is missing.",
).publish()

# A failure-polarity pattern: matching is BAD (goodness 0), clean is GOOD (goodness 1).
pattern = client.monitor.patterns.builder(
    name="Apologizes",
    description="Response apologizes",
    detector_kind="contains",
    include_terms=["sorry"],
    severity="medium",
).publish()

group = client.monitor.scorer_groups.create(
    "Blend bar",
    members=[
        {"kind": "judge", "refId": generous.id, "weight": 1, "gate": False},
        {"kind": "judge", "refId": harsh.id, "weight": 1, "gate": False},
        {"kind": "pattern", "refId": pattern.id, "weight": 1, "gate": False},
    ],
)
gated = client.monitor.scorer_groups.create(
    "Gated bar",
    members=[
        {"kind": "judge", "refId": generous.id, "weight": 1, "gate": False},
        {"kind": "pattern", "refId": pattern.id, "weight": 0, "gate": True},
    ],
)
check(
    "groups are retrievable through the SDK",
    {g.id for g in client.monitor.scorer_groups.list()} >= {group.id, gated.id},
)

dataset = (
    client.evaluations.datasets.builder(name="Group demo")
    .add_case(query="How long is the return window?", expected_results="30 days.")
    .publish()
)

# --- 2. Blend run: rating == exact recomputation from the row's own member verdicts -----------
run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "displayName": "group-demo"},
        scorer_group_id=group.id,
    )
    .execute(
        lambda case: "You have 30 days from delivery."
    )  # no "sorry" -> pattern clean
    .finalize()
)
row = run.results()[0]
judges = {v["name"]: v["rating"] for v in (row.judge_scorer_results or [])}
pattern_row = next(
    cs for cs in (row.code_scorer_results or []) if cs["name"] == "Apologizes"
)
check(
    "judge members report labeled verdicts",
    set(judges) == {"Generous", "Harsh"},
    str(judges),
)
check("the clean failure-pattern member scores 1", pattern_row["score"] == 1)

expected = (
    round(
        (judges["Generous"] / 10 + judges["Harsh"] / 10 + pattern_row["score"])
        / 3
        * 100
    )
    / 10
)
check(
    "run rating == the weighted blend of member scores, on 0-10",
    row.rating is not None and abs(row.rating - expected) < 1e-9,
    f"rating={row.rating} recomputed={expected}",
)
check("justification names the blend", "Weighted blend" in (row.justification or ""))

detail = client.evaluations.get_run(run.run_id)
check("run detail carries the group", detail.get("scorerGroupId") == group.id)
check(
    "breakdown leads with the group as primary",
    detail["scorerBreakdown"][0]["name"] == "Blend bar (group)"
    and detail["scorerBreakdown"][0]["primary"] is True,
)

# --- 3. Gate run: an apologizing answer trips the must-pass pattern -> score 0 ----------------
gated_run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "displayName": "group-demo"},
        scorer_group_id=gated.id,
    )
    .execute(
        lambda case: "Sorry, you have 30 days."
    )  # matches the gated failure pattern
    .finalize()
)
gated_row = gated_run.results()[0]
check(
    "must-pass gate zeroes the group score",
    gated_row.rating == 0,
    f"rating={gated_row.rating}",
)
check(
    "justification names the gating member",
    "Gated to 0" in (gated_row.justification or ""),
)

# --- 4. Live traffic: the group scores an ingested trace and signals below threshold ----------
client.monitor.scorer_groups.update(
    group.id,
    online={"enabled": True, "sampleRate": 1, "alertThreshold": 5, "severity": "high"},
)
# No CERTIFIED token and it apologizes: Harsh ~2, pattern matched (0) -> group well below 5.
with client.tracer.trace("group-live", input={"query": "help?"}, sync=True) as span:
    span.output = "sorry, cannot help"
client.tracer.flush(timeout=10)
signal = None
for _ in range(60):
    recent = client.monitor.signals.list(limit=50)
    signal = next(
        (
            s
            for s in recent
            if getattr(s, "pattern_key", None) == f"scorer-group:{group.id}"
        ),
        None,
    )
    if signal:
        break
    time.sleep(0.5)
check(
    "live traffic scored by the group raises a below-threshold Signal",
    signal is not None,
    getattr(signal, "summary", "")[:80] if signal else "",
)

ratings = client.monitor.scorer_groups.ratings(group.id, window="24h")
check(
    "the group has a ratings history (chart endpoint)",
    any(p["count"] > 0 for p in ratings.get("points", [])),
)

# --- 5. Fail-closed: a GATED member that cannot score means NO score, not a blend -------------
# Delete the pattern out from under the gated group, then re-grade. A deleted non-gating
# member just renormalizes away; a deleted MUST-PASS member means the safety check never ran,
# so the honest answer is no group score at all - never a blend that implies "passed".
client.monitor.patterns.delete(pattern.id)
failclosed_run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "displayName": "group-demo"},
        scorer_group_id=gated.id,
    )
    .execute(lambda case: "You have 30 days from delivery.")
    .finalize()
)
fc_row = failclosed_run.results()[0]
check(
    "a gated member that cannot score produces NO group score",
    fc_row.rating is None,
    f"rating={fc_row.rating}",
)
check(
    "justification names the unscoreable must-pass member",
    "No score: must-pass member" in (fc_row.justification or ""),
)

# --- 6. Clean up: a leftover sampleRate-1 online group would keep judging every ingested
# trace in this project on your own LLM key, forever. (The pattern is already gone - the
# fail-closed check above consumed its deletion.)
client.monitor.scorer_groups.delete(group.id)
client.monitor.scorer_groups.delete(gated.id)
client.monitor.judge_scorers.delete(generous.id)
client.monitor.judge_scorers.delete(harsh.id)
check("demo scorers cleaned up", True)

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("scorer groups: all green")
