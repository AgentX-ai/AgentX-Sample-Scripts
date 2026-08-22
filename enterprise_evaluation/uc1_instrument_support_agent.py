"""
UC1 - Instrumentation of a production-style support agent.

Buyer questions this script answers:
  - How many lines of code to get a real span tree (LLM calls + tool calls + session linkage)?
  - Time-to-first-trace: wall clock from "I have an API key" to "trace queryable via API".
  - Does the trace model capture what an enterprise needs (tokens, latency, errors, metadata)?
  - Does a failing tool show up honestly, without extra work?

The agent is simulated (no LLM key needed client-side): a two-turn support conversation where
turn 1 succeeds through a tool call and turn 2 has the escalation tool fail.
"""

import os
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4791/api/v1")
client = AgentX(api_key=os.environ["AGENTX_API_KEY"], base_url=BASE_URL)
client.ping()

t0 = time.time()

# --- Turn 1: healthy tool-using exchange -----------------------------------------------------
SESSION = f"support-{int(t0)}"
with client.tracer.trace(
    "buyer-support-agent",
    input={"query": "Where is my order #8812?"},
    session_id=SESSION,
    model="gpt-4o-mini",
    sync=True,
) as root:
    with client.tracer.trace_tool_call("lookup_order", input={"order_id": 8812}) as tool:
        tool.output = {"status": "shipped", "eta": "Thursday"}
    root.output = "Order #8812 left our warehouse yesterday and arrives Thursday."
turn1 = root.trace_id
first_trace_seconds = time.time() - t0

# --- Turn 2: the escalation tool fails -------------------------------------------------------
with client.tracer.trace(
    "buyer-support-agent",
    input={"query": "That's too late, get me a human."},
    session_id=SESSION,
    model="gpt-4o-mini",
    sync=True,
) as root2:
    with client.tracer.trace_tool_call("escalate_to_human", input={"reason": "delivery too late"}) as tool:
        tool.success = False
        tool.error = "EscalationTimeout: no agents available after 30s"
    root2.set_error("EscalationTimeout: no agents available after 30s")
turn2 = root2.trace_id

# FINDING (race): child tool spans are sent asynchronously - sync=True only makes the ROOT
# ingest synchronous. Without this flush, read-back intermittently sees 3 of 4 spans. The SDK
# provides flush(); the docs don't call out that sync=True doesn't cover children.
client.tracer.flush(timeout=5.0)
time.sleep(0.5)

# --- Verify what the platform actually captured ----------------------------------------------
# BUYER NOTE (logged in FINDINGS.md): the SDK has no client.tracer.get_trace(id) read-back -
# single-trace reads are dashboard/REST-only (GET /ingest/traces/:id). Session spans ARE
# SDK-readable, so verification goes through those.
spans = client.monitor.list_session_spans(SESSION)
print(f"time-to-first-trace: {first_trace_seconds:.2f}s")
print(f"turn1 trace: {turn1}  turn2 trace: {turn2}")
print(f"session spans recorded: {len(spans)} (expect 2 roots, tool calls recorded on them)")
roots = [s for s in spans if not s.get("parentSpanId")]
for span in spans:
    print(f"  name={span.get('name')} model={span.get('model')} latencyMs={span.get('latencyMs')} "
          f"toolCalls={len(span.get('toolCalls') or [])} err={bool(span.get('error'))}")

# The error is captured twice, correctly: on the tool's own child span AND on the root (whose
# toolCalls array carries the per-call success flag the Tool-failure classification reads).
failed_root = next((s for s in roots if s.get("error")), None)
assert failed_root, "turn 2's error was not captured on its root span"
failed_tool = next((c for c in (failed_root.get("toolCalls") or []) if c.get("success") is False), None)
tool_child = next((s for s in spans if s.get("name") == "escalate_to_human" and s.get("error")), None)
print(f"turn2 error captured on root: {failed_root['error'][:60]}")
print(f"failed tool in root toolCalls: {bool(failed_tool)} ({failed_tool and failed_tool.get('name')})")
print(f"failed tool as its own child span: {bool(tool_child)}")

ok = len(roots) == 2 and failed_root is not None and failed_tool is not None and tool_child is not None
print("\nUC1 PASS" if ok else "\nUC1 FAIL")
