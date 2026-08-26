"""
16 - Span kinds: telling the engine what each step IS, instead of letting it guess.

A trace is a tree of steps, and "what kind of step is this" is a question three different readers
used to answer separately - the Code-scorer sandbox, the dashboard's span buckets, and the
Execution Timeline. They disagreed. The timeline's last rule was "everything else is a tool", so
a span it did not recognise was drawn as a tool call that never happened, while a Code scorer
looking at that same span was told "span".

Nothing on the span said what it was. Now it can:

    span.child_span("jailbreak_check", span_kind="guardrail", ...)

and `record_tool_call` / `record_retrieval` stamp their own kinds for you. The engine resolves
each span's kind once, at ingest, and every reader - including the dashboard - reads that one
answer. The old guessing survives only as a fallback for spans that say nothing.

This script builds one realistic RAG trace using eight different kinds, reads every span back
through the SDK, and CHECKS the engine's answers. It exits non-zero if any of them is wrong, so
it is a verification you can run, not just a demo.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 16_span_kinds.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")

bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Span kinds demo {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

SESSION_ID = f"span-kinds-{int(time.time())}"

# --- 1. One trace, eight kinds of step -------------------------------------------------------
# The shape of a real RAG turn with a safety check on the front and a self-grade on the end.
# Three ways a kind gets set, all shown here:
#   - stated explicitly    -> child_span(..., span_kind="guardrail")
#   - stamped by the SDK   -> record_retrieval / record_tool_call know what they are
#   - not stated at all    -> the engine falls back to what it can infer
with client.tracer.trace(
    "rag-agent",
    input={"query": "What is the refund window?"},
    session_id=SESSION_ID,
    span_kind="agent",  # this root really IS an agent turn, so it says so
    sync=True,
) as span:
    # Stated kinds the SDK has no dedicated helper for.
    span.child_span("jailbreak_check", span_kind="guardrail",
                    input="What is the refund window?", output={"flagged": False})
    span.child_span("embed_query", span_kind="embedding",
                    input="What is the refund window?", output={"dims": 1536})

    # The SDK stamps this one itself - no span_kind argument needed.
    client.tracer.record_retrieval(
        "kb_search",
        query="refund window",
        output=["Returns: 30 days from delivery, unused and in original packaging."],
    )

    span.child_span("rerank_chunks", span_kind="reranker",
                    input={"candidates": 8}, output={"kept": 3})
    # A step with no kind and nothing to infer from. This is the one the timeline used to draw
    # as a tool call.
    span.child_span("format_prompt", input={"chunks": 3}, output="<assembled prompt>")
    span.child_span("answer", span_kind="llm", model="gpt-4o-mini",
                    input="<assembled prompt>", output="30 days from delivery.",
                    input_tokens=210, output_tokens=18)

    # Also SDK-stamped.
    client.tracer.record_tool_call("log_answer", input={"id": "a-1"}, output={"ok": True}, success=True)

    span.child_span("grade_answer", span_kind="evaluator",
                    input="30 days from delivery.", output={"rating": 9})
    span.output = "You have 30 days from delivery for a full refund."

time.sleep(2)

# --- 2. Read every span back and check what the engine decided -------------------------------
spans = client.monitor.sessions.spans(SESSION_ID)
by_name = {s["name"]: s.get("spanKind") for s in spans}

EXPECTED = {
    "rag-agent": ("agent", "stated on the root"),
    "jailbreak_check": ("guardrail", "stated"),
    "embed_query": ("embedding", "stated"),
    "kb_search": ("retrieval", "stamped by record_retrieval"),
    "rerank_chunks": ("reranker", "stated"),
    "format_prompt": ("chain", "nothing stated, nothing to infer - NOT a tool"),
    "answer": ("llm", "stated"),
    "log_answer": ("tool", "stamped by record_tool_call"),
    "grade_answer": ("evaluator", "stated"),
}

print(f"\n{len(spans)} spans in {SESSION_ID}\n")
print(f"  {'':<3} {'SPAN':<16} {'KIND':<11} WHY")
failures = []
for name, (expected, why) in EXPECTED.items():
    actual = by_name.get(name)
    ok = actual == expected
    print(f"  {'OK ' if ok else 'BAD'} {name:<16} {str(actual):<11} {why}")
    if not ok:
        failures.append(f"{name}: expected {expected!r}, got {actual!r}")

# --- 3. The rules worth proving separately ---------------------------------------------------
# a) A stated kind beats what the engine would otherwise infer. This span carries a model, which
#    the fallback ladder reads as an LLM call - but it is a guardrail and says so.
# b) Other products' vocabularies fold onto ours, so a span already instrumented for
#    OpenInference ("retriever"), Langfuse ("generation") or the OTel GenAI semconv
#    ("execute_tool") classifies on arrival with no change by the producer.
# c) A span that states nothing still classifies the way it always did.
INTEROP_SESSION = f"{SESSION_ID}-interop"
with client.tracer.trace("interop-agent", input={"q": "x"}, session_id=INTEROP_SESSION, sync=True) as span:
    span.child_span("safety_model", span_kind="guardrail", model="gpt-4o-mini",
                    input="x", output={"flagged": False})           # (a) stated beats inferred
    span.child_span("vector_lookup", span_kind="retriever", output=["chunk"])   # (b) OpenInference
    span.child_span("compose", span_kind="generation", output="hello")          # (b) Langfuse
    span.child_span("call_api", span_kind="execute_tool", output={"ok": True})  # (b) OTel semconv
    span.child_span("LLM Call 1", output="inferred from the name")              # (c) fallback
    span.output = "done"

time.sleep(2)
interop = {s["name"]: s.get("spanKind") for s in client.monitor.sessions.spans(INTEROP_SESSION)}

INTEROP_EXPECTED = {
    "safety_model": ("guardrail", "stated kind beats the model column"),
    "vector_lookup": ("retrieval", "OpenInference 'retriever'"),
    "compose": ("llm", "Langfuse 'generation'"),
    "call_api": ("tool", "OTel 'execute_tool'"),
    "LLM Call 1": ("llm", "states nothing - inferred from the name, as before"),
}

print()
for name, (expected, why) in INTEROP_EXPECTED.items():
    actual = interop.get(name)
    ok = actual == expected
    print(f"  {'OK ' if ok else 'BAD'} {name:<16} {str(actual):<11} {why}")
    if not ok:
        failures.append(f"{name}: expected {expected!r}, got {actual!r}")

# --- 4. What the kinds are actually FOR -------------------------------------------------------
# Retrieval is the one kind with a hard behavioural consequence rather than a label: it is where
# the RAG judges get their {context}. Everything the retrieval spans returned is what a
# Faithfulness or Context Relevancy scorer grades the answer against.
root = next((s for s in spans if not s.get("parentSpanId")), None)
if root:
    print(f"\nRetrieval context the RAG judges would see for {root['_id'][:12]}...:")
    for s in spans:
        if s.get("spanKind") == "retrieval":
            print(f"  from {s['name']}: {str(s.get('output'))[:80]}")

# The traces live in the project this script created, not in Default - switch the project
# picker (top left) to it, or Observe will look empty.
print(f"\nTo see each kind in the Execution Timeline (one colour and one filter pill per kind):")
print(f"  1. open {BASE_URL.replace('/api/v1', '')}/governance?tab=observe")
print(f"  2. switch the project picker (top left) to \"{project['name']}\"")
print(f"  3. open either trace")

if failures:
    print("\nFAILED:")
    for line in failures:
        print(f"  - {line}")
    sys.exit(1)
print("\nAll span kinds resolved as expected.")
