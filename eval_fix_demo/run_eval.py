"""
Evaluate the Northwind Cloud support agent on AgentX.

Publishes a dataset of support questions, runs the LangChain agent from
`to_fix_agent.py` against every case locally, submits the results to AgentX for
scoring, and finishes with `.analyze()` — the LLM-judge report.

Each case is wrapped in a real trace, so the report links every scored answer to the
execution timeline that produced it and the judge sees the agent's actual trajectory
rather than the final string alone.

Install:
    pip install agentx-python langchain langchain-anthropic langchain-openai python-dotenv

Run (needs AGENTX_API_KEY, ANTHROPIC_API_KEY and OPENAI_API_KEY in ../.env):
    python run_eval.py

Set AGENTX_DATASET_ID to re-run against a dataset that already exists instead of
publishing a new one each time — that is what makes two runs comparable.
"""

from __future__ import annotations

import os
import subprocess

from dotenv import load_dotenv

from agentx import AgentX
from agentx.evaluations.models import EvaluationCase
from agentx.integrations.langchain import AgentXCallbackHandler

import to_fix_agent
from to_fix_agent import MODEL, PROMPT_VERSION, SYSTEM_PROMPT, TOOLS, TOP_K

load_dotenv()

DATASET_NAME = "Northwind Cloud — Support Agent"


def _provenance() -> dict:
    """Which code produced this score.

    Without it a run records only "prompt v2" — the dashboard truncates the display name,
    so two runs of the same agent are visually identical and nothing says where the change
    came from. Everything here is read from git at run time, so it cannot drift from the
    code that actually ran.
    """

    def git(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", *args], stderr=subprocess.DEVNULL, text=True
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""

    return {
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "git_commit": git("rev-parse", "--short", "HEAD") or "unknown",
        "git_dirty": bool(git("status", "--porcelain")),
    }


# ---------------------------------------------------------------------------
# The agent, wrapped for AgentX: one EvaluationCase in, one result dict out
# ---------------------------------------------------------------------------


def make_run_case(client: AgentX):
    """One EvaluationCase in, one result dict out - with a real ingested trace behind it.

    The result carries `trace_id`, not an inline `trace` payload, and that distinction
    matters: the engine only renders an agent's execution path into the judge prompt for
    results that link a real ingested trace. Pass tool calls inline instead and they are
    dropped, every answer is judged as if the agent had no tools, and the report comes
    back accusing a perfectly good retrieval step of fabricating its results.

    `sync=True` is required: it blocks until the trace is ingested, which is the only way
    `span.trace_id` is populated in time to attach it to this result.
    """

    def run_case(case: EvaluationCase) -> dict:
        # The handler detects the enclosing span and emits the chain's graph nodes, LLM
        # calls and tool calls as real child span rows under it (its _emit_span_tree path),
        # which is what the Execution Timeline renders. Recording them by hand with
        # span.add_tool_call() instead folds them into a flat tool_calls column on the one
        # row, and the timeline has nothing to draw. The outer sync=True span is still what
        # populates span.trace_id for the result link.
        handler = AgentXCallbackHandler(client.tracer, name="northwind-support-agent")
        with client.tracer.trace(
            "northwind-support-agent",
            input={"query": case.query},
            framework="langchain",
            model=MODEL,
            metadata={"prompt_version": PROMPT_VERSION, "retrieval_top_k": TOP_K},
            sync=True,
        ) as span:
            reply = to_fix_agent.answer(case.query, callbacks=[handler])
            span.output = reply.text

        return {
            "output": reply.text,
            "trace_id": span.trace_id,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "metadata": {
                "framework": "langchain",
                "model": MODEL,
                "llm_calls": reply.llm_calls,
                "tools_used": ", ".join(reply.tools_used) or "none",
                "smoke_test_variant": case.is_smoke_test_variant,
            },
        }

    return run_case


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def publish_dataset(client: AgentX):
    return (
        client.evaluations.datasets.builder(
            name=DATASET_NAME,
            description=(
                "Billing, policy and account questions for the Northwind Cloud support "
                "agent. Checks handbook grounding, citation discipline, exact figures "
                "from the refund tool, and refusal to invent products."
            ),
            number_of_requests=2,
            acceptance_criteria=(
                "The direct answer comes first and is correct. Every policy, pricing or SLA "
                "claim is grounded in the handbook and cites its doc id (e.g. [POL-REFUND]). "
                "Account-specific facts and money figures match the tools exactly. Under six "
                "sentences."
            ),
            rejection_criteria=(
                "Invented plans, prices, discounts, features or guarantees. Refund or credit "
                "amounts that are wrong or computed without the tool. Policy claims with no "
                "doc-id citation. Padding, or an answer that dodges the question."
            ),
            evaluation_criteria=(
                "Weight factual accuracy and grounding above tone. Formatting and phrasing "
                "differences are not defects."
            ),
            jaccard_similarity=True,
            rouge_score=True,
        )
        .add_case(
            query="How do I cancel my subscription, and do I keep access until the end of the month?",
            expected_results=(
                "Cancel from Account > Subscription > Cancel plan. No cancellation fee. Access "
                "continues to the end of the billing period already paid for, then the workspace "
                "is read-only for 30 days before deletion. Cites POL-CANCEL."
            ),
        )
        .add_case(
            query="Do you have a free trial, and do I need to put in a credit card?",
            expected_results=(
                "14-day free trial, no credit card required, all Pro features but capped at 5 "
                "seats, extendable once by 7 days. Cites POL-TRIAL."
            ),
            smoke_test_count=2,
            smoke_test_guidance="try terse phrasing and a frustrated non-native-speaker phrasing",
        )
        .add_case(
            query="Account ACC-4471 wants to cancel and be refunded. How much do they get back?",
            expected_results=(
                "$4,160.14 — a 12-seat annual Pro term ($5,644.80) 96 days in, refunded pro rata "
                "over the 269 unused days. The $250 in add-on credits is excluded because credit "
                "packs are never refundable. Cites POL-REFUND."
            ),
            judge_guideline=(
                "The refund figure must be $4,160.14 and the response must exclude the $250 "
                "add-on credits. Rounding presentation and wording do not matter; a different "
                "dollar figure is a failure."
            ),
        )
        .add_case(
            query="Our Pro workspace only had 98.7% uptime last month. Are we owed anything?",
            expected_results=(
                "Yes — below 99.0% the credit is 25% (Pro is guaranteed 99.9%). The credit must be "
                "claimed within 30 days of the affected month and is applied to the next invoice. "
                "Cites POL-SLA."
            ),
        )
        .add_case(
            query="Can I export everything before I cancel, and in what formats?",
            expected_results=(
                "Yes — Settings > Data > Export, in CSV or JSON. Exports up to 10 GB are immediate, "
                "larger ones are emailed within 24 hours, and archives stay downloadable for 90 "
                "days. Cites POL-EXPORT."
            ),
        )
        .add_case(
            query="We dropped 4 seats halfway through our annual term. Do we get money back for those?",
            expected_results=(
                "No. Seat removals only take effect at the next renewal and removed seats are not "
                "refunded mid-term; the pro-rata refund rule applies to cancelling the plan, not to "
                "shedding seats. Cites POL-SEATS."
            ),
            judge_guideline=(
                "The answer must be no. Fail any response that offers a pro-rata refund or credit "
                "for the removed seats, or that applies the cancellation refund policy here."
            ),
        )
        .add_case(
            query="We'd rather buy a lifetime license than pay monthly. What does that cost?",
            expected_results=(
                "There is no lifetime or one-time-purchase license. The options are Starter at $19 "
                "and Pro at $49 per user per month, or custom Enterprise pricing, with 20% off for "
                "paying annually. Cites POL-PRICING and offers a next step."
            ),
            judge_guideline=(
                "This is a hallucination guard. Fail the response if it invents a lifetime or "
                "perpetual license, quotes a price for one, or offers to arrange one."
            ),
        )
        .publish()
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    client = AgentX.from_env()

    dataset_id = os.getenv("AGENTX_DATASET_ID")
    if dataset_id:
        dataset = client.evaluations.datasets.get(dataset_id)
        print(f"Reusing dataset {dataset.id} — {dataset.name}")
    else:
        dataset = publish_dataset(client)
        print(f"Published dataset {dataset.id} — {dataset.name}")

    report = (
        client.evaluations.run(
            dataset_id=dataset.id,
            subject={
                "kind": "custom_agent",
                "displayName": f"Northwind Support Agent ({MODEL}, prompt {PROMPT_VERSION})",
                "framework": "langchain",
                "runtime": "local",
                "agentInstructions": SYSTEM_PROMPT,
                "metadata": {
                    # Bump TO_FIX_AGENT_PROMPT_VERSION when you change the prompt so
                    # the dashboard can compare runs against each other.
                    "version": PROMPT_VERSION,
                    "model": MODEL,
                    "retrieval_top_k": TOP_K,
                    "tool_count": len(TOOLS),
                    **_provenance(),
                },
            },
        )
        .execute(make_run_case(client))
        .finalize()
        .analyze()
    )

    print()
    if report.jaccard_similarity is not None:
        print(f"Jaccard similarity : {report.jaccard_similarity:.3f}")
    if report.rouge_score is not None:
        print(f"ROUGE-L            : {report.rouge_score:.3f}")
    print(f"Dataset id         : {dataset.id}  (export AGENTX_DATASET_ID to re-run it)")
    if report.dashboard_url:
        print(f"Dashboard          : {report.dashboard_url}")


if __name__ == "__main__":
    main()
