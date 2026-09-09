"""
Memory span demo: one trace whose steps include long-term-memory operations, so the
Execution Timeline shows the "Memory" lane next to LLM/tool/retrieval steps.

Runs in the API key's own project (no throwaway project). After running, open
Governance > Observe > Live Traces, click the "travel-concierge" trace, and look at the
Execution Timeline: the two memory spans (a read and a write) carry the Memory kind, the
retrieval stays Retrieval - recalled user state and knowledge lookups are different steps.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 memory_span_demo.py
"""

import os

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
client = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
client.ping()

tracer = client.tracer
USER_ID = "demo-user-7"

with tracer.trace(
    "travel-concierge",
    input={"query": "Book my usual kind of flight to Denver next month."},
    sync=True,
) as span:
    # Memory READ: span_kind="memory" - recalled user state, deliberately NOT a retrieval
    # (retrieval spans feed the RAG judges' {context}; a recalled preference is not grounding).
    with tracer.trace_memory("user prefs", operation="read", query=USER_ID) as m:
        m.output = ["prefers window seats", "vegetarian meals", "flies out of Oakland"]

    # A knowledge lookup for contrast - this one IS a retrieval and renders in its own lane.
    with tracer.trace_retrieval("route_search", query="Oakland to Denver flights") as r:
        r.doc_count = 2
        r.output = ["OAK->DEN nonstop daily 7:40", "OAK->DEN nonstop daily 18:05"]

    # Memory WRITE: the agent learned something new this turn and stored it.
    with tracer.trace_memory("user prefs", operation="write", query=USER_ID) as m:
        m.output = "stored: planning a Denver trip next month"

    span.output = (
        "Booked the 7:40 Oakland to Denver nonstop - window seat, vegetarian meal, "
        "as usual. I noted the Denver trip for next month."
    )

print(f"Trace sent: {span.trace_id}")
print("Open Governance > Observe > Live Traces and click the 'travel-concierge' trace -")
print("the Execution Timeline shows the two Memory steps (read + write) in their own lane.")
