# pip install agentx-python
import os
from agentx import AgentX

client = AgentX(
    api_key={"x-api-key": os.environ["AGENTX_API_KEY"]},
    base_url="http://localhost:4700/api/v1",
)


def my_agent(case):
    """Call your real agent with case.query and return its answer."""
    # sync=True populates span.trace_id before the block exits; returning it as trace_id is what
    # lights up "View trace" on this case's result row. monitor=False opts the trace out of every
    # ingest-time check (patterns, online evaluators, topics) - the run's own evaluator already
    # judges each case, so re-judging the trace would just double the judge bill.
    with client.tracer.trace(
        "my-agent", input={"query": case.query}, sync=True, monitor=False
    ) as span:
        answer = f"placeholder of {case.query}"  # run your agent here
        span.output = answer
    return {"output": answer, "trace_id": span.trace_id}


dataset_ids = [
    "8K4KB9n6CqJJQOsoV8jpY",  # Governance Story Demo Dataset
    "-kcZRuZPiBx1I6cagaQEg",  # Prompt Autotune Demo Dataset
]

for dataset_id in dataset_ids:
    run = (
        client.evaluations.run(
            dataset_id=dataset_id,
            subject={"kind": "custom_agent", "displayName": "my-agent"},
            evaluation_settings_id="hWJo_NBYtZWkzCjoP8thJ",  # Example: Helpfulness Judge
        )
        .execute(my_agent)
        .finalize()
    )
    print(
        f"{dataset_id}: scored {run.rated_count} results - average {run.average_rating}"
    )

client.tracer.flush(timeout=10)  # send any queued traces before the script exits
