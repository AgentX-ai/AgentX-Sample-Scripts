"""
03 - Agent trajectory matching and deterministic RAG context checks. Zero judge calls.

Two checks a judge cannot do reliably and should not be paid for:

  - Trajectory match: did the agent call the RIGHT TOOLS in the right way? Declared per case
    (expected_tools + a mode with agentevals semantics), matched against the linked trace's
    real tool-call sequence.
  - Context match: did the RETRIEVER fetch the right thing? Declared per case
    (expected_retrieval_context), compared with token Jaccard against what was actually
    retrieved. Retriever regressions get caught without grading the final answer at all.

Both land as scorer rows on the result, next to the judge's - one table, mixed provenance.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 03_agent_and_rag_checks.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Eval dive 03 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

POLICY_CHUNK = "Returns: 30 days from delivery for a full refund. Items must be unused and in original packaging."
WRONG_CHUNK = "Shipping: orders ship within 2 business days via UPS ground."

dataset = (
    client.evaluations.datasets.builder(name="Agent + RAG checks")
    # strict: lookup THEN escalate, in that order.
    .add_case(query="Where is order 4471? If lost, escalate.",
              expected_tools=["lookup_order", "escalate"], trajectory_match_mode="strict")
    # unordered: both calls required, order free.
    .add_case(query="Check order 8112 and log the interaction.",
              expected_tools=["lookup_order", "log_interaction"], trajectory_match_mode="unordered")
    # context: the retriever must fetch the returns chunk.
    .add_case(query="What is the return policy?",
              expected_retrieval_context=[POLICY_CHUNK])
    .publish()
)

def agent(case):
    q = case.query
    if q.startswith("Where is order"):
        # WRONG ORDER on purpose: escalate before lookup. strict must fail this.
        with client.tracer.trace("checks-agent", input={"q": q}, sync=True) as span:
            with client.tracer.trace_tool_call("escalate", input={"reason": "lost?"}) as t:
                t.output = {"ticket": "T-1"}
            with client.tracer.trace_tool_call("lookup_order", input={"id": "4471"}) as t:
                t.output = {"status": "in_transit"}
            span.output = "Order 4471 is in transit; escalated just in case."
        return {"output": span.output, "trace_id": span.trace_id}
    if q.startswith("Check order"):
        # Right calls, "wrong" order - unordered must pass this.
        with client.tracer.trace("checks-agent", input={"q": q}, sync=True) as span:
            with client.tracer.trace_tool_call("log_interaction", input={"id": "8112"}) as t:
                t.output = {"ok": True}
            with client.tracer.trace_tool_call("lookup_order", input={"id": "8112"}) as t:
                t.output = {"status": "delivered"}
            span.output = "Order 8112 was delivered; interaction logged."
        return {"output": span.output, "trace_id": span.trace_id}
    # RAG case: return what the retriever fetched alongside the answer.
    return {"output": "You have 30 days from delivery for a full refund.",
            "retrieval_context": [POLICY_CHUNK]}

run = (
    client.evaluations.run(dataset_id=dataset.id, subject={"kind": "custom_agent", "framework": "raw_python"})
    .execute(agent)
    .finalize()
)
rows = {r.question_text: r for r in run.results()}

def scorer(row, prefix):
    for cr in row.code_scorer_results or []:
        if cr.get("name", "").startswith(prefix):
            return cr
    return None

strict = scorer(rows["Where is order 4471? If lost, escalate."], "Trajectory match")
check("strict trajectory FAILS on right calls in the wrong order",
      strict is not None and strict.get("score") == 0,
      f"{strict and strict.get('reasoning', '')[:70]}")

unordered = scorer(rows["Check order 8112 and log the interaction."], "Trajectory match")
check("unordered trajectory PASSES the same calls in any order",
      unordered is not None and unordered.get("score") == 1,
      f"{unordered and unordered.get('reasoning', '')[:70]}")

ctx = scorer(rows["What is the return policy?"], "Context match")
check("context match scores the retriever, not the answer",
      ctx is not None and (ctx.get("score") or 0) >= 0.9,
      f"jaccard={ctx and ctx.get('score')}")

# --- Negative control: a retriever that fetched the wrong chunk must score near zero ----------
dataset2 = (
    client.evaluations.datasets.builder(name="Bad retriever")
    .add_case(query="What is the return policy?", expected_retrieval_context=[POLICY_CHUNK])
    .publish()
)
run2 = (
    client.evaluations.run(dataset_id=dataset2.id, subject={"kind": "custom_agent", "framework": "raw_python"})
    .execute(lambda case: {"output": "You have 30 days from delivery for a full refund.",
                           "retrieval_context": [WRONG_CHUNK]})
    .finalize()
)
row2 = run2.results()[0]
ctx2 = scorer(row2, "Context match")
check("wrong retrieval scores near zero even though the ANSWER is right",
      ctx2 is not None and (ctx2.get("score") or 1) <= 0.2,
      f"jaccard={ctx2 and ctx2.get('score')}")

print()
if failures:
    print(f"FAILED: {failures}")
    sys.exit(1)
print("Agent + RAG checks verified: tools and retrieval graded deterministically, no judge spend.")
