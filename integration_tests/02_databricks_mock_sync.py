"""
02 - Databricks/MLflow integration: span-tree import + eval, on mock MLflow traces.

Databricks agents (Agent Bricks / Mosaic AI Agent Framework) are auto-instrumented by MLflow 3
Tracing. The pull path replays finished MLflow traces into AgentX as full span trees. This test
feeds the importer a mocked MlflowClient (objects shaped like mlflow's Trace/Span, zero network)
and verifies against a live engine:

  - every span arrives, parent links intact, grouped into one session via MLflow's session metadata
  - TOOL spans are mirrored onto the root's tool_calls, an ERROR status becomes success=False
  - an IN_PROGRESS trace is skipped, re-syncing the window creates no duplicates
  - the imported root trace can be graded offline with an LLM judge (evaluate_trace)
  - judge_sessions on the sync scores the imported session
  - the push path (enable_mlflow_export) emits the right OTLP env for this engine

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 02_databricks_mock_sync.py
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.databricks import DatabricksTraceImporter, enable_mlflow_export

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Databricks test {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- Mock MLflow traces ----------------------------------------------------------------------
# Shaped like mlflow.entities.Trace: .info (trace_id, state, request_time, trace_metadata),
# .data.spans (span_id, parent_id, name, span_type, *_time_ns, inputs, outputs, status).
NOW = datetime.now(timezone.utc)
BASE_NS = int((NOW - timedelta(minutes=10)).timestamp() * 1_000_000_000)

def span(span_id, name, span_type, parent=None, offset_ms=0, dur_ms=50, inputs=None, outputs=None, error=None):
    status = SimpleNamespace(status_code="ERROR" if error else "OK", description=error)
    return SimpleNamespace(
        span_id=span_id, parent_id=parent, name=name, span_type=span_type,
        start_time_ns=BASE_NS + offset_ms * 1_000_000,
        end_time_ns=BASE_NS + (offset_ms + dur_ms) * 1_000_000,
        inputs=inputs, outputs=outputs, status=status,
    )

def trace(trace_id, spans, state="OK"):
    return SimpleNamespace(
        info=SimpleNamespace(
            trace_id=trace_id, experiment_id="exp-1", state=state, request_time=NOW - timedelta(minutes=10),
            trace_metadata={"mlflow.trace.session": "sess-42"}, tags={},
        ),
        data=SimpleNamespace(spans=spans),
    )

QUESTION = "My order #4412 arrived damaged, can I get a replacement?"
TRACES = [
    trace("trA", [
        span("s1", "support-agent", "AGENT", inputs={"question": QUESTION},
             outputs="A replacement for order #4412 ships tomorrow; keep the damaged unit, no return needed."),
        span("s2", "search_kb", "RETRIEVER", parent="s1", offset_ms=5,
             inputs="damaged item policy", outputs=["Damaged items: replace free of charge, no return required."]),
        span("s3", "draft_answer", "LLM", parent="s1", offset_ms=60, dur_ms=400,
             inputs="<prompt>", outputs="A replacement ships tomorrow..."),
        span("s4", "create_replacement_order", "TOOL", parent="s1", offset_ms=470,
             inputs={"order": "4412"}, outputs={"replacement": "4413"}),
    ]),
    trace("trB", [
        span("s1", "support-agent", "AGENT", inputs={"question": "Please charge my saved card for the upgrade."},
             outputs="Your card was declined by the payment provider; no charge was made. Please update your card."),
        span("s2", "charge_card", "TOOL", parent="s1", offset_ms=10,
             inputs={"amount": 49}, error="card declined by issuer"),
        span("s3", "apology_note", "LLM", parent="s1", offset_ms=80, outputs="Your card was declined..."),
    ]),
    trace("trC", [span("s1", "support-agent", "AGENT")], state="IN_PROGRESS"),
]

class Page(list):
    token = None

class MockMlflowClient:
    def search_traces(self, **_kwargs):
        return Page(TRACES)

importer = DatabricksTraceImporter(
    MockMlflowClient(),
    agentx_api_key=project["apiKey"],
    agentx_base_url=BASE_URL,
)

# --- 1. Sync: the whole span tree arrives ----------------------------------------------------
since = NOW - timedelta(hours=1)
report = importer.sync(["exp-1"], since, NOW)
print(f"\n{report}\n")

check("2 finished traces imported, in-progress one skipped",
      report.traces == 2 and report.skipped_in_progress == 1)
check("all 7 spans ingested", report.spans == 7 and report.ingested == 7 and report.failed == 0)
check("TOOL spans counted", report.tool_calls == 2)
check("MLflow session metadata became one session", report.session_ids == ["dbx_sess-42"])

spans_back = client.monitor.sessions.spans("dbx_sess-42")
check("engine holds the 7 spans in the session", len(spans_back) == 7, f"got {len(spans_back)}")
roots = [s for s in spans_back if not s.get("parentSpanId")]
check("2 root traces with deterministic span ids",
      sorted(r.get("spanId") for r in roots) == ["dbx:trA", "dbx:trB"])

root_a = next((r for r in roots if r.get("spanId") == "dbx:trA"), None)
root_b = next((r for r in roots if r.get("spanId") == "dbx:trB"), None)
if root_a and root_b:
    kids_a = [s for s in spans_back if s.get("parentSpanId") == "dbx:trA"]
    check("parent links intact (3 children under trA)", len(kids_a) == 3,
          str(sorted(k.get("name") for k in kids_a)))
    calls_a = root_a.get("toolCalls") or []
    calls_b = root_b.get("toolCalls") or []
    check("successful TOOL span mirrored on root tool_calls",
          len(calls_a) == 1 and calls_a[0].get("success") is True and calls_a[0]["name"] == "create_replacement_order")
    check("ERROR TOOL span mirrored as success=False",
          len(calls_b) == 1 and calls_b[0].get("success") is False, str(calls_b))
    failed_span = next((s for s in spans_back if s.get("name") == "charge_card"), None)
    check("span error text preserved", bool(failed_span) and failed_span.get("error") == "card declined by issuer")
    llm = next((s for s in spans_back if s.get("name") == "draft_answer"), None)
    check("span latency derived from MLflow timestamps", bool(llm) and llm.get("latencyMs") == 400)

# --- 2. Re-sync with session judging: no duplicates, session gets a verdict -----------------
baseline = next(s for s in client.monitor.judge_scorers.list()
                if (s.online or {}).get("scope") == "session")
client.monitor.judge_scorers.update(baseline.id, online={"enabled": True})

report2 = importer.sync(["exp-1"], since, NOW, judge_sessions=True)
check("re-sync is idempotent (still 7 spans)", len(client.monitor.sessions.spans("dbx_sess-42")) == 7)
# The engine's own sweep may get there first; ifStale then reports skipped, never a double judge.
check("imported session judged exactly once (by us or the sweep)",
      report2.sessions_judged + report2.sessions_judge_skipped == 1 and report2.sessions_judge_failed == 0,
      f"judged={report2.sessions_judged} skipped={report2.sessions_judge_skipped}")

# --- 3. Offline eval of an imported trace ----------------------------------------------------
scorer = client.monitor.judge_scorers.create(
    "Databricks support quality",
    judge={"evaluationCriteria": "The agent must resolve the customer's problem concretely and "
           "state exactly what will happen next. Vague or evasive answers fail."},
)
verdict = client.tracer.evaluate_trace(root_a["_id"], scorer.id)
check("imported trace graded by the judge",
      isinstance(verdict.get("rating"), (int, float)) and 0 <= verdict["rating"] <= 10,
      f"rating={verdict.get('rating')}")
check("verdict carries reasoning", bool(verdict.get("justification")))
check("the damaged-order resolution scores high", (verdict.get("rating") or 0) >= 6)

# --- 4. Push path: the OTLP env enable_mlflow_export would set -------------------------------
env = enable_mlflow_export(api_key="k", base_url=BASE_URL, service_name="dbx-agent", dry_run=True)
check("OTLP endpoint points at this engine",
      env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == f"{BASE_URL}/otel/v1/traces")
check("api key rides the OTLP headers", env["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] == "x-api-key=k")
check("dual export keeps the Databricks MLflow UI working",
      env.get("MLFLOW_TRACE_ENABLE_OTLP_DUAL_EXPORT") == "true")
check("dry_run leaves the process env untouched",
      os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") is None)

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nDatabricks integration verified: span trees, tool mirroring, dedupe, session judging, trace eval, push env.")
