"""
15 - The unified LLM Judge Scorer: one judge, two profiles.

Before the unification, "Evaluator configs" (offline grading) and "online evaluators" (live
scoring) were two entities sharing a rubric by reference. Now they are ONE scorer:

  - judge:   the rubric (criteria, judge prompt, judge model)
  - offline: how dataset runs grade with it (its id IS the scorer_id runs take)
  - online:  whether/how it scores live traffic (sampling, scope, alert threshold)

This script creates one scorer, uses the SAME id to grade an offline dataset run and to score
live traffic, then tunes the one rubric both share.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 15_unified_judge_scorer.py
"""

import os
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")

bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Unified judge scorer demo {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

# --- 1. One scorer, both profiles, one call --------------------------------------------------
# The snake_case builder assembles all three sections in one call (a dict-based
# judge_scorers.create(judge=..., offline=..., online=...) exists too, camelCase wire keys).
scorer = client.monitor.judge_scorers.builder(
    "Support answer quality",
    acceptance_criteria="Concrete, correct, and cites the documented policy.",
    rejection_criteria="Vague, hedging, or contradicts the policy.",
    jaccard_similarity=True,
    live=True, sample_rate=1.0, alert_threshold=6, severity="medium",
).publish()
print(f"scorer: {scorer.id} | online profile: {scorer.online_profile_id}")

# --- 2. The SAME id grades an offline dataset run --------------------------------------------
dataset = (
    client.evaluations.datasets.builder(name="Policy answers")
    .add_case(query="What's the return window?",
              expected_results="30 days from delivery, full refund on most items.")
    .publish()
)
run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "framework": "raw_python"},
        scorer_id=scorer.id,  # <- the scorer IS the grading config
    )
    .execute(lambda case: "You have 30 days from delivery for a full refund on most items.")
    .finalize()
)
ratings = [r.rating for r in run.results() if r.rating is not None]
print(f"offline run graded by the scorer's rubric: avg {sum(ratings) / len(ratings):.1f}/10")

# --- 3. The SAME rubric scores live traffic --------------------------------------------------
with client.tracer.trace("support-agent", input={"q": "return window?"}, sync=True) as span:
    span.output = "Our policy is generous, please check the website for details."  # vague -> low
time.sleep(3)
events = client.monitor.judge_scorers.events(scorer.id, window="24h")
print(f"live checks recorded: {len(events)}")

# --- 4. Sparse updates: pause live scoring without touching the rubric -----------------------
paused = client.monitor.judge_scorers.update(scorer.id, online={"enabled": False})
print(f"paused online scoring; rubric intact: "
      f"{paused.judge['acceptanceCriteria'].startswith('Concrete')}; "
      f"profile id stable: {paused.online_profile_id == scorer.online_profile_id}")

# --- 5. One rubric to tune: calibration reads the online profile, publish edits the judge ----
cal = client.monitor.judge_scorers.calibration(scorer.id, window="24h")
print(f"calibration (needs ground truth to be meaningful): compared={cal.get('withGroundTruth', 0)}")

print("\nDone - one scorer id served offline grading, live scoring, and tuning.")
