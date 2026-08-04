"""
Dataset-based evaluation: build a small test set once, run your agent against every case, and get
back an LLM judge rating for each, the closest thing to a CI test suite for prompts. Re-running
this after a prompt change tells you whether you actually improved things or just moved the
problem around.

`expected_results` and `judge_guideline` are two different things, easy to conflate:
- `expected_results` is a concrete sample of what the agent should actually say: the judge
  prompt treats it as ground truth and scores agreement with it (see judge-core's
  DEFAULT_JUDGE_PROMPT: "Expected Results Are the Authoritative Ground Truth"). Write it as a
  real answer, not a description of one.
- `judge_guideline` is per-case grading instructions, what to weigh, what to penalize, appended
  to the judge prompt as extra context, separate from the expected answer itself.

Note: the grading config below turns on every similarity metric (vector/BLEU/ROUGE/Jaccard), all
four are fully computed on self-host (ported from the hosted platform's own algorithms into the
shared judge-core package), not stubs.

The agent under test (support_agent below) is a real ReAct loop with a policy_lookup tool, not a
single canned LLM call, most real agents look like this: the model decides whether to call a tool
at all, not this script. See 02_trace_your_agent.py for the same pattern with more explanation.

input_tokens/output_tokens are summed across every turn of that loop and returned alongside the
answer, that's what populates the token columns in the results table; the SDK reads them from
either a top-level input_tokens/output_tokens key or metadata.prompt_tokens/completion_tokens on
whatever the callable returns, there's no automatic way for it to know otherwise.
"""

import json
import os
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.evaluations.models import Dataset, EvaluationCase
from agentx.evaluations.runner import EvaluationRunContext
from agentx.integrations.openai import patch_openai_client

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    resp = requests.get(f"{BASE_URL}/dev/bootstrap", timeout=5)
    resp.raise_for_status()
    return resp.json()["apiKey"]


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)
oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
patch_openai_client(oai, client.tracer)


# --- Step 1: build the dataset ------------------------------------------------------------------
# In practice you'd build this once (or via the dashboard) and reuse the same dataset_id across
# every future run. Built fresh here so this script is runnable standalone.
dataset: Dataset = (
    client.evaluations.datasets.builder(
        name="Support Agent Regression Set",
        number_of_requests=1,
        acceptance_criteria="Accurate, concise, grounded in the stated policy. No invented details.",
        rejection_criteria="Dismissive, ignores the question, or makes up a policy that wasn't stated.",
    )
    .add_case(
        query="What's your refund policy on digital products?",
        expected_results="Digital product purchases are final and can't be refunded, except when "
        "the product is defective or unusable, in which case contact support for a replacement "
        "or refund.",
        judge_guideline="Must state that digital purchases are final by default AND mention the "
        "defective/unusable exception. A response that only covers one half should score no "
        "higher than 5-6, not a 9-10.",
        # Smoke test: asks this same question 2 extra ways each run, LLM-paraphrased server-side
        # (init_run time, core/evaluate/judge.ts's generateSmokeTestVariants) per
        # smoke_test_guidance, to catch an agent that's brittle to phrasing rather than genuinely
        # wrong, e.g. a real user typing with typos or a half-finished message, not the clean,
        # well-formed query above. Variants are generated once and frozen for the run's lifetime,
        # then run and scored exactly like the main questions (see the extra rows below, tagged
        # with the paraphrased query text instead of the original).
        smoke_test_count=2,
        smoke_test_guidance="grammar error, short incomplete message",
    )
    .add_case(
        query="How long does standard shipping take?",
        expected_results="Standard shipping takes 5-7 business days.",
        judge_guideline="A vague non-answer like 'it varies' or 'check your confirmation email' "
        "with no concrete number of days should score low (2-3), even though it isn't technically "
        "false.",
    )
    .add_case(
        query="Can I change my order after it's been placed?",
        expected_results="You can change your order within 1 hour of placement. After that, it's "
        "already entered fulfillment and can't be modified.",
        judge_guideline="Must include both the 1-hour window and what happens after it expires, "
        "mentioning only one half caps the score around 5-6.",
    )
    .publish()
)
print(f"Published dataset: {dataset.id} ({len(dataset.questions)} cases)")


# --- Step 2: the agent under test ----------------------------------------------------------------
# A real ReAct loop with a policy_lookup tool, not the policy text baked directly into the system
# prompt: closer to how a real support agent actually works, and it's what most of this repo's
# "single LLM call" examples are really standing in for (see 02_trace_your_agent.py). Re-run this
# script after editing either SYSTEM_PROMPT or POLICY_DB below to see the rating move.
POLICY_DB = {
    "digital": "Digital product purchases are final except when the product is defective or "
    "unusable, in which case contact support for a replacement.",
    "shipping": "Standard shipping takes 5-7 business days.",
    "change": "Orders can be changed within 1 hour of placement; after that they've already "
    "entered fulfillment and can't be modified.",
}


def policy_lookup(topic: str) -> str:
    for key, text in POLICY_DB.items():
        if key in topic.lower():
            return text
    return "No policy found for that topic."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "policy_lookup",
            "description": "Look up company policy text by topic, e.g. 'digital', 'shipping', 'change'.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    }
]

SYSTEM_PROMPT = (
    "You are a helpful, concise customer support agent. Use policy_lookup to answer policy "
    "questions instead of guessing, it has the real, current text."
)


def support_agent(case: EvaluationCase) -> Dict[str, Any]:
    # sync=True blocks until the engine has ingested the trace, so span.trace_id is populated by
    # the time the `with` block exits (the default fire-and-forget mode never learns it, since the
    # send happens on a background thread). Returning trace_id in the result below is what links
    # this case's score to its full execution timeline in the dashboard, click a low-rated result
    # and you see exactly what the agent actually did, not just the judge's summary of it.
    with client.tracer.trace(
        "support-agent",
        input={"query": case.query},
        framework="openai",
        model="gpt-4o-mini",
        sync=True,
    ) as span:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": case.query},
        ]
        input_tokens = 0
        output_tokens = 0
        model_name = "gpt-4o-mini"
        while True:
            resp = oai.chat.completions.create(model=model_name, messages=messages, tools=TOOLS)
            if resp.usage:
                input_tokens += resp.usage.prompt_tokens
                output_tokens += resp.usage.completion_tokens
            model_name = resp.model
            message = resp.choices[0].message

            if not message.tool_calls:
                span.output = message.content
                break

            messages.append(message.model_dump(exclude_none=True))
            for tool_call in message.tool_calls:
                args = json.loads(tool_call.function.arguments)
                # The tool executes in plain Python between two chat.completions.create() calls, so
                # the OpenAI patch above can't see it, record it manually (same convention as
                # 02_trace_your_agent.py).
                with client.tracer.trace_tool_call("policy_lookup", input=args) as t:
                    result = policy_lookup(**args)
                    t.output = result
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

    return {
        "output": span.output,
        "metadata": {"model": model_name},
        "trace_id": span.trace_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


# --- Step 3: standalone grading config -----------------------------------------------------------
# A reusable rubric independent of the dataset's own twin config, reuse this same settings id
# across other datasets/runs instead of rebuilding it each time. number_of_requests here takes
# precedence over the dataset's own value once evaluation_settings_id is passed to .run() below, so
# this is what actually controls repetitions per question, not the builder call above.
evaluation_settings = client.evaluations.settings.builder(
    name="Support Agent Strict Grading",
    number_of_requests=2,
    acceptance_criteria="Accurate, concise, grounded in the stated policy.",
    rejection_criteria="Dismissive, ignores the question, or invents a policy.",
    vector_similarity=True,
    jaccard_similarity=True,
    bleu_score=True,
    rouge_score=True,
).publish()
print(f"Published grading config: {evaluation_settings.id}")


# --- Step 4: run and score --------------------------------------------------------------------
# run_context.average_rating reads a `liveStatistics` field the hosted SaaS API returns but
# self-host's engine doesn't populate yet, so it comes back None here even though every result was
# genuinely scored (self-host's holistic .analyze() report endpoint isn't implemented either, same
# gap). Pull the per-question ratings directly from the run instead and average them here.
run_context: EvaluationRunContext = (
    client.evaluations.run(
        dataset_id=dataset.id,
        subject={"kind": "custom_agent", "displayName": "Support Agent (demo)", "framework": "openai"},
        evaluation_settings_id=evaluation_settings.id,
    )
    .execute(support_agent)
    .finalize()
)

# run_context._run.run_id: no public accessor for the run id exists on EvaluationRunContext yet
# (only the rating properties above), so this reaches past the public API for it.
run_detail = requests.get(
    f"{BASE_URL}/evaluate/{run_context._run.run_id}", headers={"x-api-key": local_api_key()}, timeout=10
).json()
ratings = [r["rating"] for r in run_detail["results"] if r.get("rating") is not None]

print()
if ratings:
    print(f"Average rating: {sum(ratings) / len(ratings):.2f} / 10  (across {len(ratings)} results)")
    print(f"Range:          {min(ratings):.1f} - {max(ratings):.1f}")
    for r in run_detail["results"]:
        print(f"  [{r['rating']:.0f}/10] {r['questionText']}")
        print(f"          {r['justification']}")
        # Every result carries the trace_id support_agent returned, this is what a low-rated
        # result's "View trace" link in the dashboard resolves, the same full execution timeline
        # (every LLM call, every tool call, in order) 02_trace_your_agent.py explains, not just
        # the judge's summary of it.
        if r.get("traceId"):
            print(f"          trace_id: {r['traceId']}")
print(f"\nDashboard: {BASE_URL.removesuffix('/api/v1')}")

print(
    "\nEvery run above is a real trace too, not just a graded result, open any trace_id printed "
    "above in the dashboard's Governance > Trace tab to see the full execution timeline (every "
    "LLM call and tool call, in order) behind that rating, exactly like 02_trace_your_agent.py."
)

print(
    "\nRe-run this same script after editing SYSTEM_PROMPT or POLICY_DB above to see the rating "
    "move, that's the regression-testing story: change the prompt or the tool it calls, know "
    "immediately whether it actually helped."
)
