"""
UC4 - Ground truth: are the judges right? (The differentiator claim under test.)

Buyer questions:
  - Can real-world outcomes (user votes, ops reports) be attached to traces after the fact?
  - Does the platform actually measure its own judges against that ground truth, with honest
    false-positive/false-negative math?
  - Does the downvote KPI move, and does a downvote land in triage?
"""

import os
import time

import requests
from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4791/api/v1")

boot = requests.post(f"{BASE_URL}/projects", json={"name": f"UC4 calibration {int(time.time())}"}, timeout=15)
boot.raise_for_status()
KEY = boot.json()["project"]["apiKey"]
HEADERS = {"x-api-key": KEY, "Content-Type": "application/json"}
client = AgentX(api_key=KEY, base_url=BASE_URL)
client.ping()

# PII scorer on: AgentX's "flagged in advance" side of the comparison.
requests.put(f"{BASE_URL}/agent-monitoring/settings/monitoring-defaults",
             json={"enabledBuiltinPatterns": ["pii-in-response"]}, headers=HEADERS, timeout=15).raise_for_status()

# --- Four traces with known reality ----------------------------------------------------------
def send(q, out):
    with client.tracer.trace("cal-agent", input={"q": q}, sync=True) as span:
        span.output = out
    return span.trace_id

flagged_bad = send("contact?", "Reach the reviewer at jane.doe@example.com directly.")   # flagged; reality: bad
flagged_fine = send("email fmt?", "A valid email looks like name@example.com.")          # flagged; reality: fine
healthy_fine = send("returns?", "30 days from delivery, full refund.")                   # healthy; reality: fine
healthy_bad = send("cancel?", "We value you! Please explore the website.")               # healthy; reality: bad
time.sleep(2.5)

# --- Reality arrives: one end-user vote + three ops reports ----------------------------------
client.feedback.report(healthy_bad, "down", comment="Didn't cancel anything", end_user_id="buyer-1")
client.outcomes.report(trace_id=flagged_bad, outcome="confirmed_leak", is_negative=True, reported_by="sec-review")
client.outcomes.report(trace_id=flagged_fine, outcome="reviewed_fine", is_negative=False, reported_by="sec-review")
client.outcomes.report(trace_id=healthy_fine, outcome="csat_good", is_negative=False, reported_by="csat-sync")

# --- Calibration read-back -------------------------------------------------------------------
cal = requests.get(f"{BASE_URL}/agent-monitoring/calibration?window=24h", headers=HEADERS, timeout=15).json()
print(f"reported={cal['reportedCount']} compared={cal['comparedCount']} "
      f"agreement={cal['agreementRate']:.2f} FP={cal['falsePositiveRate']:.2f} FN={cal['falseNegativeRate']:.2f}")

kpis = client.monitor.kpis(window="24h")
votes = client.feedback.list(healthy_bad)
signals = client.monitor.signals.list(limit=50)
fb_signals = [s for s in signals if s.pattern_key == "negative-feedback"]
print(f"downvoteRate={kpis['downvoteRate']} (1 down / 1 vote)")
print(f"feedback rows on trace: {len(votes)}; downvote triage signals: {len(fb_signals)}")

# Expected confusion matrix: TP=1 (flagged+bad), FP=1 (flagged+fine), TN=1, FN=1
# -> agreement 0.5, falsePositiveRate 0.5 (1 of 2 flagged), falseNegativeRate 0.5 (1 of 2 healthy)
ok = (
    cal["comparedCount"] == 4
    and abs(cal["agreementRate"] - 0.5) < 1e-6
    and abs(cal["falsePositiveRate"] - 0.5) < 1e-6
    and abs(cal["falseNegativeRate"] - 0.5) < 1e-6
    and kpis["downvoteRate"] == 1.0
    and len(fb_signals) == 1
)
print("\nUC4 PASS" if ok else "\nUC4 FAIL")
