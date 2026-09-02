"""
04 - Topics: what your users actually talk about, classified from live traffic.

Topics is opt-in per project (each sampled trace costs one judge call to classify), so a
fresh install spends nothing. This script enables it, sends themed traffic, and reads the
classified themes back through the SDK.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 04_topics.py
Needs an LLM judge key on the engine (one classification call per trace: 4 here).
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Monitor ops 04 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. Opt in, project-wide ----------------------------------------------------------------
topics_before = client.monitor.topics()
check("a fresh project has no topics (and has spent nothing on them)",
      not (topics_before.get("topIntents") or []), topics_before.get("topIntents"))

client.monitor.set_topics(True)

# --- 2. Themed traffic ----------------------------------------------------------------------
CONVERSATIONS = [
    ("Where is my refund for order 812?", "Your refund was issued yesterday; allow 3 days."),
    ("I still have not received my refund!", "I see it pending - it lands tomorrow."),
    ("How do I reset my password?", "Use the Forgot Password link on the sign-in page."),
    ("My password reset email never arrives", "I have re-sent it; also check spam."),
]
for question, answer in CONVERSATIONS:
    with client.tracer.trace("faq-bot", input=question, sync=True) as span:
        span.output = answer

# Classification is fire-and-forget after ingest; give the judge calls time to land.
intents = []
deadline = time.time() + 90
while time.time() < deadline:
    intents = client.monitor.topics().get("topIntents") or []
    if sum(t.get("count", 0) for t in intents) >= 4:
        break
    time.sleep(2)

# --- 3. The themes are real, counted, and named in user language -----------------------------
check("classified topics exist", len(intents) >= 1, f"{len(intents)} topic(s)")
total = sum(t.get("count", 0) for t in intents)
check("every themed trace was classified", total >= 4, f"{total} classified")
names = " | ".join(f"{t.get('intent', '?')} x{t.get('count', 0)}" for t in intents)
blob = names.lower()
check("the themes reflect what users asked about (refunds / passwords)",
      ("refund" in blob) or ("password" in blob) or ("account" in blob) or ("billing" in blob),
      names)
print(f"\n  Topics seen: {names}")

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nTopics verified: opt-in classification turns raw traffic into named themes.")
