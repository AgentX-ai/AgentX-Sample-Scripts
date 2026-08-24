import os
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.evaluations.models import (
    Dataset,
    EvaluationCase,
    Report,
)
from agentx.evaluations.runner import EvaluationRunContext

load_dotenv()

# Self-host: no workspace_id, the API key alone selects the project. BASE_URL defaults to the
# local engine; the key itself is fetched from the unauthenticated bootstrap endpoint the same way
# the dashboard does on load, so nothing needs to be hand-copied into .env for this to run.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    key = os.getenv("AGENTX_API_KEY")
    if not key:
        raise SystemExit(
            "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup."
        )
    return key


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)

oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

dataset_id: str = ""  # replace with your dataset id to reuse an existing one
# No dataset id above is portable across installs, so this always builds a fresh one when unset -
# in practice you'd usually reuse an existing dataset_id (created once via the dashboard or this
# builder) across many runs instead of rebuilding it every time.
create_dataset: bool = not dataset_id

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

    resp = oai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful support agent."},
            {"role": "user", "content": case.query},
        ],
    )

    return {
        "output": resp.choices[0].message.content,
        "metadata": {"model": resp.model},
    }


scorer_id: str = ""  # replace with a judge scorer id to reuse an existing one
if not scorer_id:
    # No id above is portable across installs, so this always builds a fresh scorer when unset -
    # in practice you'd usually reuse an existing scorer_id across many runs.
    scorer = client.monitor.judge_scorers.builder(
        "two runs - Strict",
        number_of_requests=2,
        acceptance_criteria="Accurate, concise, grounded in the support policy.",
        rejection_criteria="No hallucinated policies or made-up steps.",
        vector_similarity=True,
        jaccard_similarity=True,
        bleu_score=True,
        rouge_score=True,
    ).publish()
    scorer_id = scorer.id

print(f"Published judge scorer: {scorer_id}")

# Each step's return type is annotated explicitly: .execute()/.finalize() both return
# EvaluationRunContext (no scores yet - those need scoring+analysis first); only .analyze()
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
        scorer_id=scorer_id,
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
