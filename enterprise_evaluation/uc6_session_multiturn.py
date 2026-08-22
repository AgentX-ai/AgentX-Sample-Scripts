"""
UC6 - Multi-turn session evaluation.

Buyer questions:
  - Are multi-turn conversations first-class (grouped, inspectable), not just single traces?
  - Can a whole conversation be judged end-to-end (coherence / resolution), and does the judge
    catch a conversation-level failure that every single turn hides (context loss)?

The session under test: turn 3 loses context established in turn 1 - each reply looks fine in
isolation; only a session-level judge can flag it. Requires the ENGINE to have an OpenAI key.
"""

import os
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4791/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"UC6 sessions {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

SESSION = f"buyer-session-{int(time.time())}"
TURNS = [
    ("My name is Priya and my order number is 4471. Where is it?",
     "Thanks Priya! Order 4471 shipped Monday and arrives Thursday."),
    ("Can you make sure it's left with the concierge?",
     "Done - order 4471 will be left with the concierge on delivery."),
    ("Great. And what was my order number again?",
     "I'm sorry, I don't have access to your order details. Could you provide your order number?"),
]
for user_msg, reply in TURNS:
    with client.tracer.trace("buyer-session-agent", input={"query": user_msg},
                             session_id=SESSION, sync=True) as span:
        span.output = reply
client.tracer.flush(timeout=5)
time.sleep(1)

spans = client.monitor.sessions.spans(SESSION)
print(f"session grouped: {len(spans)} turns under {SESSION}")

score = client.monitor.sessions.coherence_check(SESSION)
print(f"coherence: {score['rating']}/10 across {score['spanCount']} spans")
print(f"justification: {score['justification'][:140]}")
drift_flagged = bool(score.get("driftSpanId"))
print(f"drift span identified: {drift_flagged}")

# A context-losing conversation should score poorly at the session level even though each
# individual reply is polite and well-formed.
ok = len(spans) == 3 and score["rating"] is not None and score["rating"] <= 6
print("\nUC6 PASS" if ok else "\nUC6 FAIL")
