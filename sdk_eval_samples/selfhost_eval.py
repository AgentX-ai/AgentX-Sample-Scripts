# pip install agentx-python
#
# Minimal self-host evaluation: build a dataset, grade it with a unified LLM Judge Scorer
# (the scorer's id IS the scorer_id a run takes), and link every result to its
# trace. Point AGENTX_SELFHOST_BASE_URL/AGENTX_API_KEY at your engine - nothing is hardcoded.
import os
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

client = AgentX(
    api_key=os.environ["AGENTX_API_KEY"],
    base_url=os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1"),
)
client.ping()


def my_agent(case):
    """Call your real agent with case.query and return its answer."""
    # sync=True populates span.trace_id before the block exits; returning it as trace_id is what
    # lights up "View trace" on this case's result row. monitor=False opts the trace out of every
    # ingest-time check (patterns, online evaluators, topics) - the run's own evaluator already
    # judges each case, so re-judging the trace would just double the judge bill.
    with client.tracer.trace(
        "my-agent", input={"query": case.query}, sync=True, monitor=False
    ) as span:
        answer = f"You have 30 days from delivery to return most items."  # run your agent here
        span.output = answer
    return {"output": answer, "trace_id": span.trace_id}


# One unified judge scorer: rubric + offline grading profile in a single call. Reusable across
# every dataset run below (and, if you add an online profile, across live traffic too).
scorer = client.monitor.judge_scorers.create(
    f"Helpfulness judge {int(time.time())}",
    judge={"acceptanceCriteria": "Concrete, actionable, and answers the question asked."},
)

dataset = (
    client.evaluations.datasets.builder(name=f"Selfhost eval demo {int(time.time())}")
    .add_case(query="What's your return window?",
              expected_results="30 days from delivery for most items.")
    .publish()
)

run = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "displayName": "my-agent"},
        scorer_id=scorer.id,  # the scorer IS the grading config
    )
    .execute(my_agent)
    .finalize()
)
rows = run.results()
ratings = [r.rating for r in rows if r.rating is not None]
linked = sum(1 for r in rows if r.trace_id)
print(f"{dataset.id}: scored {len(ratings)} result(s) - average "
      f"{sum(ratings) / len(ratings):.1f}, traces linked {linked}/{len(rows)}")

client.tracer.flush(timeout=10)  # send any queued traces before the script exits
