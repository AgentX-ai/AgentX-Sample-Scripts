"""
UC8 (round 2) - Backup & restore drill: can I get my data OUT, and back IN?

Buyer questions:
  - Does the advertised export manifest match reality (row counts, every entity)?
  - Does a full dump land on disk as NDJSON I can read with jq, with content intact?
  - Does the documented replay-restore actually reconstruct a project (counts AND content)?
  - Do incremental pulls (since=) work, for a nightly-delta backup job?

Round-1 finding this probes: [GAP] "No bulk export / backup API" (FINDINGS.md UC7).
"""

import json
import os
import tempfile
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4791/api/v1")

bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project = bootstrap.projects.create(f"UC8 backup {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
client.ping()

# --- Seed: traces with a session, ground truth on top ----------------------------------------
trace_ids = []
for i in range(6):
    with client.tracer.trace("backup-agent", input={"q": f"question-{i}"},
                             session_id="uc8-session", sync=True) as span:
        span.output = f"answer-{i} with distinctive marker UC8-{i}"
    trace_ids.append(span.trace_id)
client.feedback.report(trace_ids[0], "down", comment="uc8 wrong answer", end_user_id="uc8-user")
client.outcomes.report(trace_id=trace_ids[1], outcome="uc8_confirmed_fine", is_negative=False,
                       reported_by="uc8-drill")
time.sleep(1.5)

# --- 1. Manifest tells the truth -------------------------------------------------------------
manifest = client.export.manifest()
by_entity = {e["entity"]: e["rows"] for e in manifest}
print(f"manifest entities: {len(manifest)}; traces={by_entity.get('traces')} "
      f"feedback={by_entity.get('feedback')} outcomes={by_entity.get('outcomes')}")

# --- 2. Full dump to disk --------------------------------------------------------------------
backup_dir = tempfile.mkdtemp(prefix="uc8-backup-")
written = client.export.dump(backup_dir)
traces_file = os.path.join(backup_dir, "traces.ndjson")
with open(traces_file) as fh:
    dumped = [json.loads(line) for line in fh if line.strip()]
content_ok = sum(1 for r in dumped if "UC8-" in json.dumps(r.get("output", "")))
# outcomes = 2: the explicit ops report plus the downvote's dual-written outcome (a "down" vote
# is ground truth twice - as feedback AND as a negative outcome; verified in UC4 round 1).
print(f"dump: {written['traces']} traces, {written['feedback']} feedback, "
      f"{written['outcomes']} outcomes; markers intact: {content_ok}/6")

# --- 3. Replay-restore into a fresh project (the documented DR path) -------------------------
restored_project = bootstrap.projects.create(f"UC8 restore target {int(time.time())}")
target = AgentX(api_key=restored_project["apiKey"], base_url=BASE_URL)
for row in dumped:
    with target.tracer.trace(row["name"], input=row["input"],
                             session_id=row.get("sessionId"), sync=True) as span:
        span.output = row["output"]
time.sleep(1.5)
restored = list(target.export.iter("traces"))
restored_markers = sum(1 for r in restored if "UC8-" in json.dumps(r.get("output", "")))
sessions_survived = len({r.get("sessionId") for r in restored if r.get("sessionId")})
print(f"restore: {len(restored)} traces replayed, markers {restored_markers}/6, "
      f"sessions preserved: {sessions_survived}")

# --- 4. Incremental pull ---------------------------------------------------------------------
none_future = list(client.export.iter("traces", since="2099-01-01"))
all_past = list(client.export.iter("traces", since="2000-01-01"))
print(f"incremental: since-future={len(none_future)} (expect 0), "
      f"since-past={len(all_past)} (expect {written['traces']})")

ok = (
    by_entity.get("traces") == 6
    and written["traces"] == 6
    and written["feedback"] == 1
    and written["outcomes"] == 2
    and content_ok == 6
    and restored_markers == 6
    and sessions_survived == 1
    and len(none_future) == 0
    and len(all_past) == written["traces"]
)
print("\nUC8 PASS" if ok else "\nUC8 FAIL")
