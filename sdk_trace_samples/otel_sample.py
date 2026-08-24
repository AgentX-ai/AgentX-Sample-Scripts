# pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
# No AgentX SDK needed - the engine speaks OTLP/HTTP directly. Multi-span traces group
# into sessions automatically, and gen_ai.* / OpenInference attributes are understood.
import os
import sys

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from dotenv import load_dotenv

load_dotenv()
# Same base-url convention every other sample uses - the OTLP path hangs off /api/v1.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", os.getenv("BASE_URL", "http://localhost:4700/api/v1")).rstrip("/")
exporter = OTLPSpanExporter(
    endpoint=f"{BASE_URL}/otel/v1/traces",
    headers={"x-api-key": os.environ["AGENTX_API_KEY"]},
)
provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)

tracer = trace.get_tracer("my-agent")
with tracer.start_as_current_span("my-agent") as span:
    span.set_attribute("input.value", "How do I reset my password?")
    answer = "placeholder"  # replace with your agent's actual answer
    span.set_attribute("output.value", answer)

# force_flush returns False when the batch could not be delivered - fail loudly instead of
# exiting 0 with nothing ingested (the OTLP exporter only logs its retries).
if not provider.force_flush():
    sys.exit("OTLP export failed - is the engine reachable at " + BASE_URL + "?")
print(f"OTLP span exported to {BASE_URL}/otel/v1/traces")
