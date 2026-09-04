"""
07 - Pure OpenTelemetry ingestion triggers online evaluation, end to end.

The engine speaks OTLP/HTTP directly (POST {base}/otel/v1/traces) - no AgentX SDK on the agent's
hot path, just the standard OpenTelemetry SDK with gen_ai.* / OpenInference attributes. This
script proves the traffic is a first-class citizen downstream too: a span exported with plain
OTel gets scored by an online judge like any SDK-ingested trace, and a low verdict raises a
Signal.

    pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http

The AgentX SDK is used here only for SETUP (judge scorer) and
VERIFICATION (read events/signals back) - the ingest itself is OTel-only, which is the point.
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1").rstrip(
    "/"
)

failures = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


api_key = os.environ.get("AGENTX_API_KEY", "")
# --- 1. a fully-sampled online judge (setup via SDK) -----------------------
bootstrap = AgentX(api_key=api_key, base_url=BASE_URL)
client = AgentX(api_key=api_key, base_url=BASE_URL)
client.ping()

scorer = client.monitor.judge_scorers.builder(
    "OTel Quality Bar",
    acceptance_criteria="Grounded in stated policy, empathetic, offers a concrete next step.",
    rejection_criteria="Curt, invents policy, or leaves the customer with nothing actionable.",
    live=True,
    sample_rate=1.0,
    alert_threshold=5,
).publish()
print(f"Judge scorer live: {scorer.name} ({scorer.id})")

# --- 2. Ingest via PURE OpenTelemetry - no AgentX SDK involved -------------------------------
exporter = OTLPSpanExporter(
    endpoint=f"{BASE_URL}/otel/v1/traces",
    headers={"x-api-key": api_key},
)
provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("otel-support-agent")

# A deliberately bad answer, so the judge has something to flag: curt, invents a policy,
# no next step - exactly the rejection criteria above.
with tracer.start_as_current_span("otel-support-agent") as root:
    root.set_attribute(
        "input.value", "I was charged twice for my order, please refund the duplicate."
    )
    root.set_attribute("output.value", "Refund issued; 3-5 business days.")
    # A gen_ai.* child span, to exercise the multi-span path the OTLP route groups per trace.
    with tracer.start_as_current_span("llm-call") as llm:
        llm.set_attribute("gen_ai.request.model", "gpt-4o-mini")
        llm.set_attribute("gen_ai.usage.input_tokens", 42)
        llm.set_attribute("gen_ai.usage.output_tokens", 9)

if not provider.force_flush():
    sys.exit(f"OTLP export failed - is the engine reachable at {BASE_URL}?")
print("Span exported via OTLP/HTTP (no AgentX SDK on the ingest path)")

# --- 3. The judge scores OTel traffic like any other traffic ---------------------------------
deadline = time.time() + 90
events = []
while time.time() < deadline:
    events = client.monitor.judge_scorers.events(scorer.id, window="24h")
    if events:
        break
    time.sleep(2)

check(
    "the OTel-ingested trace was judged",
    len(events) >= 1,
    f"{len(events)} scored event(s)",
)
if events:
    event = events[0]
    check(
        "the verdict carries the OTel trace id",
        bool(event.trace_id),
        f"trace {event.trace_id}",
    )
    check(
        "the judge read the OpenInference input/output",
        "charged twice" in (event.input or "")
        and "Refund issued" in (event.output or ""),
    )
    check(
        "the bad answer scored below the alert threshold",
        event.rating < 5,
        f"rating {event.rating}: {(event.justification or '')[:80]}",
    )

# --- 4. ...and a low verdict raises a Signal, same as SDK traffic ----------------------------
signals = client.monitor.signals.list()
judge_signals = [s for s in signals if s.pattern_key.startswith("online-eval:")]
check(
    "the low score raised a Signal",
    len(judge_signals) >= 1,
    f"{len(judge_signals)} signal(s)",
)

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print(
    "\nOTLP ingestion verified: OTel spans are scored, flagged, and triaged like any traffic."
)
