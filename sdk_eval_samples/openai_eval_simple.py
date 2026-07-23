import os
from typing import Any, Dict

from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.evaluations.models import (
    Dataset,
    EvaluationCase,
    EvaluationSettings,
    Report,
)
from agentx.evaluations.runner import EvaluationRunContext

load_dotenv()


client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)

oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

dataset_id: str = "6a615e9bc3f237a121f85fde"  # replace with your dataset id
create_dataset: bool = False  # set to True to create a new dataset

if create_dataset:
    # Build a small dataset inline so this script is runnable standalone. In practice you'd usually
    # reuse an existing dataset_id (created once via the dashboard or this builder) across many runs.
    dataset: Dataset = (
        client.evaluations.datasets.builder(
            name="Support Agent Eval Sample",
            number_of_requests=2,  # runs per case
            acceptance_criteria="Accurate, concise, grounded in the support policy.",
            rejection_criteria="No hallucinated policies or made-up steps.",
        )
        .add_case(
            query="How do I reset my password?",
            expected_results="Explain the password reset process step by step.",
        )
        .add_case(
            query="What payment methods do you accept?",
            expected_results="List supported payment methods clearly.",
        )
        .publish()
    )
    print(f"Published dataset: {dataset.id}")
    dataset_id = dataset.id


def support_agent(case: EvaluationCase) -> Dict[str, Any]:
    # sync=True blocks until AgentX has ingested the trace, so span.trace_id is populated by the
    # time the `with` block exits — the default (fire-and-forget) mode never learns the trace_id,
    # since it's queued and sent on a background thread. Attaching trace_id to the eval result
    # below is what makes this case's "Message Trace Details -> Execution Timeline" viewable
    # alongside its score in the dashboard.
    with client.tracer.trace(
        "support_agent_call",
        input={"query": case.query},
        framework="openai",
        model="gpt-4o-mini",
        sync=True,
    ) as span:
        resp = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful support agent."},
                {"role": "user", "content": case.query},
            ],
        )
        span.output = resp.choices[0].message.content

    return {
        "output": resp.choices[0].message.content,
        "metadata": {"model": resp.model},
        "trace_id": span.trace_id,
    }


# A standalone, reusable grading config — independent of the dataset's own twin config, and with
# all similarity metrics turned on (each defaults to False, both here and on the dataset itself).
# Reuse this same settings id across other datasets/runs instead of rebuilding it each time.
evaluation_settings: EvaluationSettings = client.evaluations.settings.builder(
    name="two runs - Strict",
    number_of_requests=2,
    acceptance_criteria="Accurate, concise, grounded in the support policy.",
    rejection_criteria="No hallucinated policies or made-up steps.",
    vector_similarity=True,
    jaccard_similarity=True,
    bleu_score=True,
    rouge_score=True,
).publish()
print(f"Published evaluation settings: {evaluation_settings.id}")

# Each step's return type is annotated explicitly: .execute()/.finalize() both return
# EvaluationRunContext (no scores yet — those need scoring+analysis first); only .analyze()
# returns a Report. Keeping run_context and report as two separately typed variables means a type
# checker (mypy/pyright) catches it immediately if .analyze() is ever skipped/commented out and
# something then tries to read report.average_rating off the wrong type.
run_context: EvaluationRunContext = (
    client.evaluations.run(
        dataset_id=dataset_id,
        subject={
            "kind": "custom_agent",
            "displayName": "GPT-4o-mini Support Agent",
            "framework": "openai",
        },
        evaluation_settings_id=evaluation_settings.id,
    )
    .execute(support_agent)
    .finalize()
)
# report: Report = run_context.analyze()

# print(f"\nAverage rating: {report.average_rating:.2f}")
# if report.cosine_similarity is not None:
#     print(f"Cosine similarity: {report.cosine_similarity:.3f}")
# if report.jaccard_similarity is not None:
#     print(f"Jaccard similarity: {report.jaccard_similarity:.3f}")
# if report.bleu_score is not None:
#     print(f"BLEU score: {report.bleu_score:.3f}")
# if report.rouge_score is not None:
#     print(f"ROUGE-L score: {report.rouge_score:.3f}")
# print(f"Dashboard: {report.dashboard_url}")
