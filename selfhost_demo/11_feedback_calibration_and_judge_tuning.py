"""
Ground truth and judge tuning: the LLM-as-judge gets judged, then improved.

03/04 show judges scoring your agent. This script closes the meta-loop: real-world signals (an
end user's thumbs-down, a support lead's review, a reopened ticket) are reported back against the
SAME traces the judge scored, per-evaluator calibration measures where the judge disagreed with
reality, and a criteria rewrite is generated from those disagreements - validated by exact
re-judging (the candidate criteria re-score the very cases the current ones got wrong, plus a
control set they got right) before a human would publish it.

The demo assigns ground truth AFTER seeing the judge's ratings, deliberately contradicting some
of them - that's the mechanics of the loop, compressed. In production the contradictions arrive
on their own: client.feedback.report() from your app's vote buttons, client.outcomes.report()
from your ticket system, and re-scores from the dashboard's signal-triage Feedback dialog (the
richest source, since those carry a human rationale).
"""

# NOTE: since the judge-scorer unification, the preferred surface for everything in
# this script is client.monitor.judge_scorers (one entity: rubric + offline + online
# profiles) - see 15_unified_judge_scorer.py. The surfaces used below keep working.


import os
import time

from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.integrations.openai import patch_openai_client

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
PUBLISH = False  # set True to actually publish the tuned criteria at the end


def local_api_key() -> str:
    key = os.getenv("AGENTX_API_KEY")
    if not key:
        raise SystemExit(
            "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup."
        )
    return key


API_KEY = local_api_key()
client = AgentX(api_key=API_KEY, base_url=BASE_URL)
oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
patch_openai_client(oai, client.tracer)


# --- Step 1: a judge to calibrate - ONE judge scorer, rubric + live scoring in one call ------
evaluator = client.monitor.judge_scorers.builder(
    "Judge Tuning Demo Evaluator",
    acceptance_criteria=(
        "Warm, friendly, and empathetic tone. Acknowledges the customer's concern, apologizes "
        "where appropriate, and reassures them the team cares. A caring, professional voice is "
        "what matters most."
    ),
    rejection_criteria=(
        "Cold or curt tone, telling the customer no, denying a request, or responses that feel "
        "unaccommodating."
    ),
    live=True,
    sample_rate=1.0,
).publish()
print(f"Evaluator: {evaluator.id} (criteria deliberately shallow - politeness over substance)")


# --- Step 2: production traffic with a deliberate quality spread ----------------------------
# The judge's criteria above reward politeness; two of these are polite but substantively wrong,
# which is exactly the blind spot the ground truth below will expose.
EXCHANGES = [
    ("What's your return window?", "I completely understand wanting to know! You have 30 days from delivery to return most items for a full refund. Want me to start a return for you?"),
    ("Why was my order #4471 cancelled?", "Thank you so much for reaching out, and I'm truly sorry for any worry this caused! We deeply value you as a customer, your experience matters enormously to us, and please rest assured our wonderful team cares about making this right for you."),
    ("Can you tell me the card number on my account?", "No. Stored card numbers are never disclosed through support chat, per security policy."),
    ("Do you ship to Canada?", "We do not disclose shipping information."),
]
trace_ids = []
for query, reply in EXCHANGES:
    with client.tracer.trace("judge-tuning-demo-agent", input={"query": query}, framework="openai", model="gpt-4o-mini", sync=True) as span:
        span.output = reply
    trace_ids.append(span.trace_id)
print(f"Sent {len(trace_ids)} traces")


# --- Step 3: wait for the judge's verdicts --------------------------------------------------
print("Waiting for the online evaluator to score them...")
scored = {}
for _ in range(20):
    time.sleep(3)
    events = client.monitor.judge_scorers.events(evaluator.id, window="24h")
    scored = {e.trace_id: e.rating for e in events if e.trace_id in trace_ids}
    if len(scored) == len(trace_ids):
        break
if not scored:
    raise SystemExit("The evaluator produced no ratings - check OPENAI_API_KEY on the engine.")
for tid, rating in scored.items():
    print(f"  judge rated {rating}/10 -> trace {tid}")


# --- Step 4: reality disagrees --------------------------------------------------------------
# Ground truth targets SPECIFIC exchanges (not rating ranks), because each verdict has a reason
# that belongs to its exchange - the downvote belongs to the polite non-answer, the QA
# confirmation to the security refusal. With politeness-only criteria the judge typically rates
# the non-answer high (a miss) and the refusal low (an over-flag); the two remaining exchanges
# get agreeing ground truth and become validation's anti-overfit control set.
good_trace, nonanswer_trace, refusal_trace, curt_trace = trace_ids

client.feedback.report(
    nonanswer_trace,
    "down",
    comment="Sounded friendly but never actually answered anything or did anything",
    end_user_id="demo-user-1",
)
print(f"\nEnd user DOWNVOTED the polite non-answer (judge said {scored.get(nonanswer_trace)}/10)")

client.outcomes.report(
    trace_id=refusal_trace,
    outcome="confirmed_good",
    is_negative=False,
    reason="Reviewed: withholding payment details without verification is required policy, not a refusal",
    reported_by="qa-review",
)
print(f"QA CONFIRMED the security refusal was correct behavior (judge said {scored.get(refusal_trace)}/10)")

client.feedback.report(good_trace, "up")
client.outcomes.report(trace_id=curt_trace, outcome="reopened", is_negative=True, reason="Confirmed unhelpful")
print("Agreeing ground truth reported for the other two (the validation control set)")


# --- Step 5: per-evaluator calibration ------------------------------------------------------
cal = client.monitor.judge_scorers.calibration(evaluator.id, window="24h")
print(
    f"\nCalibration: {cal['agreements']}/{cal['withGroundTruth']} agreement"
    f" | missed: {cal['missed']} (judge passed it, reality said bad)"
    f" | over-flagged: {cal['overFlagged']} (judge flagged it, reality said fine)"
)
for c in cal["disagreementCases"]:
    truth = c["groundTruth"]
    print(f"  [{truth['source']}] judge {c['rating']}/10 vs reality bad={truth['isBad']}: {(truth['detail'] or '')[:80]}")

if not cal["disagreementCases"]:
    # The judge's ratings are non-deterministic; if this run happened to agree with all ground
    # truth there is nothing to tune from - that's the loop working, not a failure (exit 0).
    print("\nNo disagreements this run - the judge already agrees with the recorded ground truth.")
    raise SystemExit(0)


# --- Step 6: rewrite the judge's own criteria from the disagreements ------------------------
print("\nGenerating a criteria rewrite from the disagreements (one judge call)...")
proposal = client.monitor.judge_scorers.tune(evaluator.id, window="24h")
print(f"Reasoning: {proposal['reasoning'][:220]}")
for change in proposal["changes"][:5]:
    print(f"  [{change['tag']}] {change['text'][:90]}")


# --- Step 7: validate by EXACT re-judging ---------------------------------------------------
# Not an approximation: the candidate criteria re-judge the exact cases the current criteria got
# wrong, plus the control cases they got right. Agreement with recorded reality is measured
# directly - "fixes 2/2, preserves 2/2" is a mathematical claim, not a vibe.
print("\nValidating: candidate criteria re-judge the disagreements + a control set...")
criteria = {k: proposal[k] for k in ("acceptanceCriteria", "rejectionCriteria", "evaluationCriteria")}
verdict = client.monitor.judge_scorers.validate_tuning(evaluator.id, criteria, window="24h")
print(f"Verdict: {verdict['verdict'].upper()} (net {verdict['netAgreementGain']:+d})")
print(verdict["summary"])


# --- Step 8: publish (human-gated AND provenance-gated) -------------------------------------
# The engine refuses an unvalidated publish, and a measured regression, unless force=True:
# the validation verdict rides along and is stamped into the rubric's version history, so a
# tuned-and-validated change is forever distinguishable from a hand edit.
if PUBLISH:
    client.monitor.judge_scorers.publish_tuning(evaluator.id, criteria, validation=verdict)
    print("\nPublished: the evaluator's config now carries the tuned criteria (version history keeps the old one,")
    print(f"stamped with the validation verdict: {verdict['verdict']}).")
else:
    print("\nPUBLISH=False, nothing was written. In the dashboard: Scorers page >")
    print("Judge Tuning Demo Evaluator > row menu > Tune judge - the same evidence, rewrite, and")
    print("exact-validation verdict, with publish behind a human click. The richest evidence source")
    print("(a triage re-score with a rationale) is dashboard-only: Monitor > Signals > Feedback.")
