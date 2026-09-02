"""
01 - Automation rules: route matching production traffic into review and datasets, from code.

A RULE never scores - it moves traffic somewhere useful: sample matching traces into the
human-review queue (the stream feeding judge calibration), or append them as dataset cases
(production failures become tomorrow's regression tests). This script creates both kinds via
the SDK, sends matching and non-matching traffic, and verifies exactly the right traces moved.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 01_rules_route_traffic.py
No judge calls - rules are deterministic routing.
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Monitor ops 01 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. Two rules: refund traffic goes to review; errored traces become dataset cases --------
review_rule = client.monitor.rules.create(
    "Spot-check refunds",
    "review",
    filter={"contains": "refund"},
    sample_rate=1,
)
regression_dataset = (
    client.evaluations.datasets.builder(name="Production failures").add_case(
        query="seed case", expected_results="keeps the dataset non-empty"
    ).publish()
)
dataset_rule = client.monitor.rules.create(
    "Errored traces become regression cases",
    "dataset",
    filter={"status": "error"},
    sample_rate=1,
    action_config={"datasetId": regression_dataset.id},
)
check("both rules exist and are enabled",
      sorted(r["name"] for r in client.monitor.rules.list() if r.enabled)
      == ["Errored traces become regression cases", "Spot-check refunds"])

# --- 2. Traffic: one refund question, one errored trace, one boring trace --------------------
with client.tracer.trace("support-bot", input="I want a refund for order 7", sync=True) as s1:
    s1.output = "Refund initiated."
with client.tracer.trace("support-bot", input="What are your store hours?", sync=True) as s2:
    s2.output = "9 to 5."
with client.tracer.trace("support-bot", input="Charge my card", sync=True) as s3:
    s3.output = ""
    s3.set_error("card processor timeout")
time.sleep(2)  # rules run detached from the ingest response

# --- 3. Exactly the right traces moved -------------------------------------------------------
queued = client.monitor.review_queue.list(status="pending")
check("the refund trace (and only it) was sampled into review",
      len(queued) == 1 and queued[0].trace_id == s1.trace_id,
      f"{len(queued)} item(s)")
check("the rule is credited as the source", queued[0].get("source") == "rule", queued[0].get("source"))

cases = client.evaluations.datasets.get(regression_dataset.id).questions
case_texts = [q.main_question.query for q in cases]
check("the ERRORED trace became a dataset case", "Charge my card" in case_texts, case_texts)
check("the healthy traces did not", "What are your store hours?" not in case_texts)

# --- 4. Disabled rules route nothing ---------------------------------------------------------
client.monitor.rules.update(review_rule.id, enabled=False)
with client.tracer.trace("support-bot", input="another refund please", sync=True) as s4:
    s4.output = "Done."
time.sleep(2)
check("a disabled rule routes nothing", len(client.monitor.review_queue.list(status="pending")) == 1)

client.monitor.rules.delete(dataset_rule.id)
check("deleted rules disappear from the catalog",
      all(r["name"] != "Errored traces become regression cases" for r in client.monitor.rules.list()))

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nRules verified: production traffic routes itself into review and regression datasets.")
