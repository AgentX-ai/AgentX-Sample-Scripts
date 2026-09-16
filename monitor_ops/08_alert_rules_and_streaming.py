"""
KPI alert rules + streaming traces: page an on-call channel when an aggregate crosses a threshold,
and see streaming LLM calls land as full traces.

Part 1 - Alert rules. Unlike a scorer's per-verdict threshold or an automation rule's per-trace
routing, an alert rule watches an AGGREGATE over a sliding window (failure rate, p95 latency,
estimated LLM cost, judge failures, trace count) and pages typed channels - Slack, Teams,
PagerDuty, email, or a generic webhook - with an Alertmanager-style lifecycle: one page when the
rule starts breaching, a repeat every cooldown while it lasts, one page when it recovers.

This script stands up a tiny local HTTP receiver that plays "Slack" (so it runs with no real
credentials), creates a failure-rate rule pointed at it, pushes a burst of errored traces, runs the
alert sweep on demand, and prints the Slack payload that arrived. Then it recovers the failure
rate and shows the RESOLVED page. Point SLACK_WEBHOOK_URL at a real incoming webhook to page a
real channel instead.

Part 2 - Streaming. `patch_openai_client` now traces `stream=True` calls: the stream is consumed
normally, and the trace carries the assembled reply, token usage, latency to the last chunk, and
time to first token. Needs OPENAI_API_KEY; skipped otherwise.

Runs in the API key's own project (no throwaway project). Cleans up the rule it created.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 08_alert_rules_and_streaming.py
"""

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv

from agentx import AgentX
from agentx.monitor.alert_rules import email, slack

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
client = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
client.ping()

# ---------------------------------------------------------------------------
# A local stand-in for a Slack incoming webhook: records every payload it receives.
# ---------------------------------------------------------------------------
received = []


class _Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        received.append(json.loads(body or b"{}"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # keep the demo output clean
        pass


server = HTTPServer(("127.0.0.1", 0), _Receiver)
threading.Thread(target=server.serve_forever, daemon=True).start()
slack_url = os.getenv("SLACK_WEBHOOK_URL") or f"http://127.0.0.1:{server.server_port}/slack"

alerts = client.monitor.alert_rules
run_id = uuid.uuid4().hex[:6]
agent_name = f"alerts-demo-{run_id}"
# Register the demo agent up front and scope the rule to it: the project's other traffic then
# never dilutes the failure rate this script drives, and the trace calls below attribute to it
# by name. An unscoped rule (agent_id=None) watches every agent in the project.
agent = client.monitor.agents.ensure(agent_name)
agent_id = agent["_id"]


def send_trace(error: str | None = None) -> None:
    # sync=True so the burst is fully ingested before the sweep is asked to evaluate it.
    with client.tracer.trace(agent_name, input={"query": "where is my order?"}, sync=True) as span:
        if error:
            span.set_error(error)
        else:
            span.output = "it shipped"


rule = None
try:
    # 1. The rule: page when the 1-hour failure rate exceeds 50%. cooldown_minutes bounds repeat
    #    pages while it keeps breaching; the debug mailer (AGENTX_EMAIL_DEBUG_DIR on the engine)
    #    makes the email channel observable without SMTP - drop it if your engine has no mailer.
    channels = [slack(slack_url)]
    if os.getenv("AGENTX_DEMO_EMAIL"):
        channels.append(email(os.environ["AGENTX_DEMO_EMAIL"]))
    rule = alerts.create(
        f"Failure rate above 50% ({run_id})",
        metric="failureRate",
        operator="gt",
        threshold=0.5,
        window_minutes=60,
        agent_id=agent_id,
        severity="critical",
        cooldown_minutes=60,
        channels=channels,
    )
    print(f"created alert rule {rule.id}: state={rule.state}")

    # What the metric reads right now, through the same computation the sweep uses.
    print("current failure rate:", alerts.preview("failureRate", 60, agent_id=agent_id)["valueLabel"])

    # 2. A burst of errored traffic pushes the window's failure rate to 80%.
    for i in range(4):
        send_trace(error="upstream timeout")
    send_trace()
    client.tracer.flush(timeout=10)
    deadline = time.time() + 15
    while time.time() < deadline:
        value = alerts.preview("failureRate", 60, agent_id=agent_id)["value"]
        if value is not None and value > 0.5:
            break
        time.sleep(0.5)
    print("failure rate after the burst:", alerts.preview("failureRate", 60, agent_id=agent_id)["valueLabel"])

    # 3. Evaluate now (the engine also does this every minute on its own).
    alerts.run_sweep()
    rule = alerts.get(rule.id)
    print(f"after sweep: state={rule.state} fired={rule.fired_count} last={rule.get('lastValueLabel')}")
    history = alerts.events(rule.id)
    print("history:", [(e.kind, e.delivered) for e in history])
    if received:
        print("Slack received:", received[-1]["text"])

    # A second sweep while still breaching: no repeat page until the cooldown elapses.
    alerts.run_sweep()
    print("history after a second sweep (cooldown holds):", [e.kind for e in alerts.events(rule.id)])

    # 4. Recovery: healthy traffic drags the rate under the threshold -> a RESOLVED page.
    for i in range(8):
        send_trace()
    client.tracer.flush(timeout=10)
    deadline = time.time() + 15
    while time.time() < deadline:
        value = alerts.preview("failureRate", 60, agent_id=agent_id)["value"]
        if value is not None and value < 0.5:
            break
        time.sleep(0.5)
    alerts.run_sweep()
    rule = alerts.get(rule.id)
    print(f"after recovery: state={rule.state}; history={[e.kind for e in alerts.events(rule.id)]}")
    if received:
        print("Slack received:", received[-1]["text"])

    # 5. "Send test" delivers a TEST page with the live value and reports every channel's result.
    test_event = alerts.test(rule.id)
    print("test page delivered to every channel:", test_event.delivered, [(d["kind"], d["ok"]) for d in test_event["deliveries"]])

    # -----------------------------------------------------------------------
    # Part 2: streaming calls are traced (OpenAI shown; NIM and Anthropic behave the same).
    # -----------------------------------------------------------------------
    if os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        from agentx.integrations.openai import patch_openai_client

        llm = OpenAI()
        patch_openai_client(llm, client.tracer, name=f"streaming-demo-{run_id}")
        with client.tracer.trace(
            f"streaming-agent-{run_id}", input={"query": "Explain streaming in one sentence."}, sync=True, span_kind="agent"
        ) as span:
            # Consume the stream exactly as you normally would; the patch assembles the trace
            # from the chunks. stream_options makes OpenAI include token usage on the last chunk.
            with llm.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "Explain streaming responses in one sentence."}],
                stream=True,
                stream_options={"include_usage": True},
            ) as stream:
                text = "".join(chunk.choices[0].delta.content or "" for chunk in stream if chunk.choices)
            span.output = text
        client.tracer.flush(timeout=10)
        print("streamed reply:", text)
        print(f"streaming trace {span.trace_id}: open it in Observe > Live Traces - the LLM Call child span carries tokens and timeToFirstTokenMs")
    else:
        print("OPENAI_API_KEY not set - skipping the streaming demo")
finally:
    if rule is not None:
        alerts.delete(rule.id)
        print("cleaned up the demo alert rule")
    server.shutdown()
