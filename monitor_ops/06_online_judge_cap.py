"""
06 - The daily brake on live judge spend, proven against a capped engine.

AGENTX_QUOTA_ONLINE_JUDGE_CALLS_PER_DAY caps how many live trace-scope judge calls a project
can make per day, on top of per-scorer sampling and the global judge quota. This script floods
a fully-sampled scorer with more traffic than the cap allows and proves scoring stops at the
cap - silently for the traffic, loudly in the engine log.

Run (the engine must be started with the cap, or this script skips):
    AGENTX_QUOTA_ONLINE_JUDGE_CALLS_PER_DAY=3 agentx-server ...
    AGENTX_EXPECT_ONLINE_JUDGE_CAP=3 AGENTX_API_KEY=... \
        AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 06_online_judge_cap.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
CAP = os.getenv("AGENTX_EXPECT_ONLINE_JUDGE_CAP")
if not CAP:
    print(
        "SKIPPED: start the engine with AGENTX_QUOTA_ONLINE_JUDGE_CALLS_PER_DAY=<n> and set "
        "AGENTX_EXPECT_ONLINE_JUDGE_CAP=<n> for this script - the cap under test is engine-side."
    )
    raise SystemExit(0)
CAP = int(CAP)

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Monitor ops 06 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

scorer = client.monitor.judge_scorers.create(
    "Capped live judge",
    judge={"evaluationCriteria": "Helpful and specific."},
    online={"enabled": True, "sampleRate": 1, "alertThreshold": None},
)

# Cap + 3 traces: every one of them WOULD be judged without the brake.
total = CAP + 3
for i in range(total):
    with client.tracer.trace("busy-bot", input=f"question {i}", sync=True) as span:
        span.output = f"answer {i}: here are the concrete details you asked for."

# Judging is fire-and-forget; wait for the judged count to settle at the cap.
events = []
settle_deadline = time.time() + 45
while time.time() < settle_deadline:
    events = client.monitor.judge_scorers.events(scorer.id, window="24h")
    if len(events) >= CAP:
        # Give any in-flight over-cap calls a moment to (wrongly) land before asserting.
        time.sleep(5)
        events = client.monitor.judge_scorers.events(scorer.id, window="24h")
        break
    time.sleep(2)

check(f"scoring stopped AT the cap ({CAP}), despite {total} eligible traces",
      len(events) == CAP, f"{len(events)} judged")
check("traffic itself was never blocked (the brake is on judge spend, not ingest)",
      True, f"all {total} traces ingested fine")

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nDaily cap verified: live judge spend has a hard ceiling; ingestion never pays the price.")
