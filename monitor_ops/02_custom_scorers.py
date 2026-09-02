"""
02 - Custom scorers: your own Python function and your own HTTP endpoint, scoring live traffic.

Two scorer kinds that spend zero judge budget: a CODE scorer (a Python handler the engine runs
per sampled trace) and an EXTERNAL scorer (your HTTP endpoint, POSTed the full trace record).
This script deploys one of each, drives matching traffic through them, and verifies the
verdicts, the signal a low score raises, and dry_run (test without persisting).

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 02_custom_scorers.py
No judge calls. The engine host needs python3 on PATH (the code scorer runs in a subprocess).
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
project = bootstrap.projects.create(f"Monitor ops 02 {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. A Python code scorer: flag answers that leak internal ticket ids ---------------------
CODE = """
def handler(input, output, expected, metadata, trace):
    # 0..1 score: 0 when the reply leaks an internal ticket id, 1 otherwise.
    leaked = "INT-" in (output or "")
    return {"score": 0.0 if leaked else 1.0, "metadata": {"leaked": leaked}}
"""
code_scorer = client.monitor.scorers.create_code(
    "No internal ids in replies", CODE, language="python", sample_rate=1, alert_below=0.5, severity="high"
)

# dry_run executes against the built-in sample WITHOUT persisting - the safe pre-deploy check.
dry = client.monitor.scorers.dry_run(kind="code", language="python", script=CODE)
check("dry_run executes the scorer without persisting anything", "score" in json.dumps(dry), str(dry)[:80])

# --- 2. An external scorer: this script IS the endpoint --------------------------------------
received = []
class Endpoint(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        received.append(body)
        # Contract: answer {matches, reason} - matches=True means "this trace has the problem".
        angry = "angry" in json.dumps(body.get("trace", body)).lower()
        payload = json.dumps({"matches": angry, "reason": "caps-lock rage detected" if angry else "calm"})
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(payload.encode())
    def log_message(self, *args):
        pass

server = HTTPServer(("127.0.0.1", 0), Endpoint)
threading.Thread(target=server.serve_forever, daemon=True).start()
endpoint_url = f"http://127.0.0.1:{server.server_port}/score"

client.monitor.scorers.create_external("Rage detector", endpoint_url, sample_rate=1, severity="medium")

# --- 3. Traffic through both -----------------------------------------------------------------
with client.tracer.trace("support-bot", input="Where is my package?", sync=True) as ok_span:
    ok_span.output = "Your package arrives Friday."
with client.tracer.trace("support-bot", input="I am SO ANGRY about ticket INT-4412", sync=True) as bad_span:
    bad_span.output = "Sorry about INT-4412, escalating now."
time.sleep(3)  # custom scorers run detached from the ingest response

# --- 4. Both scorers produced verdicts; the bad trace tripped both ---------------------------
check("the external endpoint was actually called with trace payloads", len(received) >= 2,
      f"{len(received)} POST(s)")
scorer_rows = {s["name"]: s for s in client.monitor.scorers.list()}
code_events = client.monitor.scorers.events(scorer_rows["No internal ids in replies"]["_id"])
check("the code scorer scored both traces", len(code_events) == 2, f"{len(code_events)} event(s)")
low = [e for e in code_events if e.get("score") is not None and e["score"] < 0.5]
check("the leak scored low, the clean reply did not", len(low) == 1, [e.get("score") for e in code_events])

external_events = client.monitor.scorers.events(scorer_rows["Rage detector"]["_id"])
matched = [e for e in external_events if e.get("matched")]
check("the external scorer matched exactly the rage trace", len(matched) == 1,
      f"{len(matched)} matched of {len(external_events)}")

signals = client.monitor.signals.list()
check("the low code score raised a signal for triage",
      any("internal ids" in (s.summary or "").lower() or "No internal ids" in (s.summary or "") for s in signals),
      f"{len(signals)} signal(s)")

server.shutdown()
if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nCustom scorers verified: your Python and your endpoint score live traffic, free of judge spend.")
