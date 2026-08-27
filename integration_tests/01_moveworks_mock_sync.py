"""
01 - Moveworks integration: trace import + offline eval + session judging, on mock Data API data.

Moveworks agents run inside the Moveworks cloud, so the integration is a pull importer over the
read-only Data API. This test feeds the importer a mocked Data API client (no network, records
shaped like real conversations/interactions/plugin-calls exports) and verifies the full loop
against a live engine:

  - conversations become sessions, interactions become traces, plugin calls become tool_calls
  - a record with no timestamp is skipped, not fabricated
  - evaluate_against grades each NEW interaction with an LLM judge; the brush-off answer
    scores low, the helpful ones score high
  - re-syncing the same window is idempotent: no duplicate traces, no re-billed judge calls
  - judge_sessions scores each imported session once, and ifStale skips the re-run

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 01_moveworks_mock_sync.py
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from agentx import AgentX
from agentx.integrations.moveworks import MoveworksImporter

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"Moveworks test {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- Mock Data API ---------------------------------------------------------------------------
# Same table names and field names a real export serves; the importer reads each field from a
# list of known candidates, so this is exactly the seam a deployment-specific export hits too.
NOW = datetime.now(timezone.utc)
def iso(minutes_ago, seconds=0):
    return (NOW - timedelta(minutes=minutes_ago) + timedelta(seconds=seconds)).isoformat()

MOCK_TABLES = {
    "conversations": [
        {"conversation_id": "c-1", "domain": "it", "created_time": iso(30)},
        {"conversation_id": "c-2", "domain": "hr", "created_time": iso(20)},
    ],
    "interactions": [
        {"interaction_id": "i-1", "conversation_id": "c-1", "user_id": "u-7",
         "utterance": "My laptop won't connect to the VPN, I have a customer call in 20 minutes.",
         "response": "I ran VPN diagnostics on your device: your client certificate expired this morning. "
                     "I have renewed it and pushed the new profile. Please toggle the VPN off and on; "
                     "it should connect now. If not, reply here and I will open a P2 ticket.",
         "created_time": iso(29), "resolved_time": iso(29, seconds=42)},
        {"interaction_id": "i-2", "conversation_id": "c-1", "user_id": "u-7",
         "utterance": "Also, can you reset my Okta password?",
         "response": "Can't help with that. Figure it out yourself.",
         "created_time": iso(28)},
        {"interaction_id": "i-3", "conversation_id": "c-2", "user_id": "u-9",
         "utterance": "How many PTO days do I have left this year?",
         "response": "You have 11.5 PTO days remaining for this year (accrued 21.5, used 10). "
                     "Your balance refreshes on January 1st and up to 5 unused days roll over.",
         "created_time": iso(19)},
        {"interaction_id": "i-4", "conversation_id": "c-2", "user_id": "u-9",
         "utterance": "Which company holidays are left this year?",
         "response": "Four remain: Labor Day (Sep 7), Thanksgiving (Nov 26-27), and the winter "
                     "shutdown Dec 24 through Jan 1.",
         "created_time": iso(18)},
        # No timestamp at all: the importer must skip it rather than invent a time.
        {"interaction_id": "i-5", "conversation_id": "c-2", "utterance": "ghost", "response": "ghost"},
    ],
    "plugin-calls": [
        # The judge grades trajectory-aware: an answer claiming "I renewed your certificate"
        # scores LOW unless a tool call backs the claim. The renew_certificate call below is
        # what makes the i-1 answer honest; remove it and the judge downgrades i-1 for
        # unsupported claims (a nice property, but not what this test asserts).
        {"interaction_id": "i-1", "plugin_name": "vpn_diagnostics", "served": True, "used": True,
         "created_time": iso(29)},
        {"interaction_id": "i-1", "plugin_name": "renew_certificate", "served": True, "used": True,
         "created_time": iso(29)},
        {"interaction_id": "i-1", "plugin_name": "kb_search", "served": True, "used": False,
         "created_time": iso(29)},
        {"interaction_id": "i-3", "plugin_name": "pto_lookup", "served": True, "used": True,
         "created_time": iso(19)},
    ],
}

class MockDataAPIClient:
    """Stands in for MoveworksDataAPIClient: same .records() surface, zero network."""
    def records(self, table, **_kwargs):
        yield from MOCK_TABLES.get(table, [])

# --- An LLM judge scorer for evaluate_against ------------------------------------------------
scorer = client.monitor.judge_scorers.create(
    "Moveworks interaction quality",
    judge={
        "evaluationCriteria": "The assistant response must actually resolve or concretely advance "
        "the employee's request, with specifics (numbers, dates, actions taken). A refusal, "
        "brush-off, or unhelpful deflection is a failure regardless of tone."
    },
)

importer = MoveworksImporter(
    MockDataAPIClient(),
    agentx_api_key=project["apiKey"],
    agentx_base_url=BASE_URL,
)

# --- 1. First sync: traces in, every new interaction judged ----------------------------------
since = NOW - timedelta(hours=1)
report = importer.sync(since, NOW, evaluate_against=scorer.id)
print(f"\n{report}\n")

check("conversations and interactions counted", report.conversations == 2 and report.interactions == 5,
      f"conversations={report.conversations} interactions={report.interactions}")
check("4 interactions ingested, timestampless one skipped",
      report.ingested == 4 and report.skipped_no_time == 1 and report.failed == 0)
check("4 plugin calls attached as tool_calls", report.plugin_calls_attached == 4)
check("conversations became sessions", report.session_ids == ["mw_c-1", "mw_c-2"],
      str(report.session_ids))

ratings = report.trace_eval_ratings
check("every new interaction was judged", report.traces_evaluated == 4 and len(ratings) == 4)
check("ratings are on the 0..10 scale", all(0 <= r <= 10 for r in ratings), str(ratings))
if len(ratings) == 4:
    check("the brush-off answer scores low", ratings[1] <= 5, f"rating={ratings[1]}")
    check("the helpful answers score high", ratings[0] >= 6 and ratings[2] >= 6,
          f"vpn={ratings[0]} pto={ratings[2]}")
check("report carries the average", report.trace_eval_average is not None,
      f"avg={report.trace_eval_average and round(report.trace_eval_average, 1)}")

# --- 2. Read the traces back from the engine -------------------------------------------------
spans = client.monitor.sessions.spans("mw_c-1")
check("session mw_c-1 holds its 2 interactions", len(spans) == 2, f"got {len(spans)}")
vpn = next((s for s in spans if "VPN" in str(s.get("input"))), None)
check("interaction became a trace with input/output", vpn is not None and bool(vpn.get("output")))
if vpn:
    calls = vpn.get("toolCalls") or []
    check("plugin calls landed on the trace", len(calls) == 3 and calls[0].get("served") is True,
          f"{[c.get('name') for c in calls]}")
    check("latency derived from resolved_time", vpn.get("latencyMs") == 42_000,
          f"latencyMs={vpn.get('latencyMs')}")
    check("trace attributed per Moveworks domain", vpn.get("name") == "moveworks-it")
    check("framework tagged moveworks", vpn.get("framework") == "moveworks")

# --- 3. Re-sync the same window: idempotent, and no judge call is re-billed ------------------
report2 = importer.sync(since, NOW, evaluate_against=scorer.id)
check("re-sync judges nothing (span dedupe)",
      report2.trace_eval_skipped_deduped == 4 and report2.traces_evaluated == 0,
      f"skipped_deduped={report2.trace_eval_skipped_deduped}")
check("re-sync creates no duplicate traces", len(client.monitor.sessions.spans("mw_c-1")) == 2)

# --- 4. Session judging: once per session, ifStale on the re-run -----------------------------
# The built-in Session Baseline Judge ships disabled (a fresh install cannot spend); its online
# profile is enabled through the unified judge-scorer surface.
baseline = next(s for s in client.monitor.judge_scorers.list()
                if (s.online or {}).get("scope") == "session")
client.monitor.judge_scorers.update(baseline.id, online={"enabled": True})

# The engine's own periodic sweep may beat this call to a session; ifStale then reports it as
# skipped instead of judging twice. Either way each session ends up with exactly one verdict.
judged = importer.judge_sessions(report.session_ids)
check("each imported session judged exactly once (by us or the sweep)",
      judged.sessions_judged + judged.sessions_judge_skipped == 2 and judged.sessions_judge_failed == 0,
      f"judged={judged.sessions_judged} skipped={judged.sessions_judge_skipped}")
rejudged = importer.judge_sessions(report.session_ids)
check("re-judging skips fresh verdicts (ifStale)",
      rejudged.sessions_judged == 0 and rejudged.sessions_judge_skipped == 2,
      f"skipped={rejudged.sessions_judge_skipped}")

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nMoveworks integration verified: import, tool calls, offline eval, dedupe, session judging.")
