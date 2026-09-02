"""
03 - Webhooks: failures page YOUR systems - signal fan-out and CI gate fan-out, received live.

Two webhook fan-outs, one local receiver: (a) a failure-polarity pattern match on an agent
whose Monitor profile lists a webhook channel POSTs the signal to your URL the moment it
fires; (b) a RECORDED CI gate failure fans out to every profile's webhooks - the "a bad build
just shipped worse answers" pager.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 03_webhooks.py
No judge calls (a string-match pattern and an ungraded gate failure do the firing).
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Monitor ops 03 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 0. The receiver: this script plays the part of your incident tracker --------------------
received = []
class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        received.append(body)
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass

server = HTTPServer(("127.0.0.1", 0), Receiver)
threading.Thread(target=server.serve_forever, daemon=True).start()
webhook_url = f"http://127.0.0.1:{server.server_port}/hooks/agentx"

# --- 1. A failing pattern + a webhook channel on the agent's profile -------------------------
client.monitor.patterns.builder(
    "Apology loop",
    detector_kind="contains",
    include_terms=["sorry, something went wrong"],
    severity="high",
).publish()

# The agent registers itself on first trace; then its profile gets the webhook channel.
with client.tracer.trace("checkout-bot", input="warmup", sync=True) as warm:
    warm.output = "ok"
agent = next(a for a in client.monitor.agents.list() if a["name"] == "checkout-bot")
client.monitor.profile.update(agent["_id"], channels=[f"webhook:{webhook_url}"])

# monitor=True runs the pattern sweep on this trace at ingest (the failure that fans out).
with client.tracer.trace("checkout-bot", input="Buy the blue one", sync=True, monitor=True) as bad:
    bad.output = "Sorry, something went wrong. Please try again later."

deadline = time.time() + 15
while time.time() < deadline and not received:
    time.sleep(0.5)
signal_hooks = [r for r in received if "patternKey" in json.dumps(r)]
check("the pattern failure POSTed to the webhook", len(signal_hooks) >= 1, f"{len(received)} POST(s)")
if signal_hooks:
    payload = json.dumps(signal_hooks[0])
    check("the payload names the failure and severity",
          "Apology loop" in payload or "apology" in payload.lower(), payload[:120])

# --- 2. A RECORDED CI gate failure fans out too ----------------------------------------------
dataset = (
    client.evaluations.datasets.builder(name="Gate demo")
    .add_case(query="q", expected_results="a")
    .publish()
)
run = client.evaluations.run(dataset.id, {"displayName": "checkout-bot"})
# Submit an ERRORED result: rated 0 without any judge call, so the fail_under gate fails
# deterministically and judge-free.
run.execute(lambda case: {"error": {"type": "crash", "message": "agent crashed"}}).finalize()
before = len(received)
gate = client.evaluations.gate_run(run.run_id, fail_under=7, record=True, caller="monitor_ops/03_webhooks.py")
check("the gate failed (as constructed)", gate.passed is False)

deadline = time.time() + 15
while time.time() < deadline and len(received) == before:
    time.sleep(0.5)
gate_hooks = [json.dumps(r) for r in received[before:]]
check("the recorded gate failure POSTed to the webhook", len(gate_hooks) >= 1,
      f"{len(received) - before} new POST(s)")
if gate_hooks:
    check("the payload points at the failing run", run.run_id in gate_hooks[0] or "gate" in gate_hooks[0].lower(),
          gate_hooks[0][:120])

server.shutdown()
if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nWebhooks verified: pattern failures and failed CI gates reach your systems, unprompted.")
