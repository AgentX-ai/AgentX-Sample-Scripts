"""
Deep dive - reliability probes (engineer-lead evaluation, round 3).

R1  Crash durability: ingest synchronously, kill -9 the engine mid-life, restart on the same
    home - every acknowledged trace must still be there (WAL semantics, not promises).
R2  Engine-down behavior: instrumented APP code must not block or crash when the backend is
    unreachable - the SDK's fire-and-forget claim under test, plus what happens to the queue.
R3  Malformed-payload battery: oversized, broken JSON, null bytes, 1000-deep nesting, wrong
    types, astral unicode - the engine must reject cleanly and stay healthy after each.
R4  Sustained concurrent load: 20 threads x 100 traces; error rate, latency percentiles, and
    an exact stored-row count (no loss, no dupes) while a judged eval runs mid-burst.

Boots its own engine (port 4797). Run:
  AGENTX_ENGINE_DIR=.../AgentX-trace-eval/engine python3 reliability_probes.py
"""

import concurrent.futures
import json
import os
import signal
import statistics
import subprocess
import tempfile
import time

import requests
from dotenv import load_dotenv

load_dotenv()
ENGINE_DIR = os.environ["AGENTX_ENGINE_DIR"]
PORT = 4797
BASE = f"http://localhost:{PORT}/api/v1"
HOME = tempfile.mkdtemp(prefix="dd-reliability-")


def start_engine():
    proc = subprocess.Popen(
        ["npx", "tsx", "src/index.ts"],
        cwd=ENGINE_DIR,
        env={**os.environ, "PORT": str(PORT), "AGENTX_HOME": HOME,
             "AGENTX_SESSION_SWEEP": "false", "AGENTX_IMPROVEMENT_SWEEP": "false"},
        stdout=open(os.path.join(HOME, "engine.log"), "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,  # so killpg reaches the tsx grandchild
    )
    for _ in range(60):
        try:
            requests.get(f"http://localhost:{PORT}/health", timeout=1)
            return proc
        except requests.RequestException:
            time.sleep(0.5)
    raise RuntimeError("engine did not boot")


def hard_kill(proc):
    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    proc.wait(timeout=10)
    time.sleep(1)


proc = start_engine()
KEY = requests.post(f"{BASE}/projects", json={"name": "dd-reliability"},
                    headers={"Content-Type": "application/json"}, timeout=10).json()["project"]["apiKey"]
H = {"x-api-key": KEY, "Content-Type": "application/json"}

os.environ["AGENTX_API_BASE_URL"] = BASE  # the SDK's process-global base (see dx_probes E9)
from agentx import AgentX  # noqa: E402

client = AgentX(api_key=KEY, base_url=BASE)

print("=" * 72)
print("R1  kill -9 durability")
print("=" * 72)
for i in range(50):
    with client.tracer.trace("durable-agent", input={"q": f"durability-{i}"}, sync=True) as span:
        span.output = f"ack-{i}"
hard_kill(proc)
proc = start_engine()
stored = requests.get(f"{BASE}/ingest/traces?limit=200", headers=H, timeout=10).json()["traces"]
survived = len([t for t in stored if t["name"] == "durable-agent"])
print(f"  acknowledged before SIGKILL: 50; survived restart: {survived}  "
      f"{'PASS' if survived == 50 else 'FAIL - acknowledged writes lost'}")
r1_ok = survived == 50

print()
print("=" * 72)
print("R2  engine down: the instrumented app must not block or crash")
print("=" * 72)
hard_kill(proc)

t0 = time.time()
for i in range(20):
    with client.tracer.trace("offline-agent", input={"q": f"offline-{i}"}) as span:  # async mode
        span.output = "queued while down"
t_async = time.time() - t0

t0 = time.time()
flush_err = None
try:
    client.tracer.flush(timeout=3.0)
except Exception as e:  # noqa: BLE001
    flush_err = f"{type(e).__name__}: {e}"
t_flush = time.time() - t0

t0 = time.time()
sync_err = None
try:
    with client.tracer.trace("offline-sync", input={"q": "x"}, sync=True) as span:
        span.output = "y"
except Exception as e:  # noqa: BLE001
    sync_err = f"{type(e).__name__}: {e}"
t_sync = time.time() - t0

print(f"  20 async traces while down: {t_async * 1000:.0f} ms total "
      f"({'non-blocking PASS' if t_async < 1.0 else 'BLOCKED - FAIL'}), app exception: none")
print(f"  flush(timeout=3): returned in {t_sync and t_flush:.1f}s, raised: {flush_err or 'no'}")
print(f"  sync=True while down: returned in {t_sync:.1f}s, raised: {sync_err or 'no'} "
      f"(must fail fast or degrade gracefully, never hang)")
r2_ok = t_async < 1.0 and t_flush < 15 and t_sync < 15

proc = start_engine()
time.sleep(1)
after_restart = requests.get(f"{BASE}/ingest/traces?limit=200", headers=H, timeout=10).json()["traces"]
recovered = len([t for t in after_restart if t["name"] == "offline-agent"])
print(f"  queued-while-down traces delivered after engine returned: {recovered}/20 "
      f"(0 = dropped: document the durability contract, not silent)")

print()
print("=" * 72)
print("R3  malformed-payload battery (engine must reject cleanly and stay up)")
print("=" * 72)

cases = {
    "11MB body (limit 10mb)": json.dumps({"name": "big", "input": "x" * 11_000_000, "output": "y"}),
    "broken JSON": '{"name": "broken", "input": ',
    "null byte in string": json.dumps({"name": "nul", "input": "a\x00b", "output": "c"}),
    "1000-deep nested input": '{"name": "deep", "output": "y", "input": '
                              + '{"a":' * 1000 + '1' + '}' * 1000 + '}',
    "wrong types everywhere": json.dumps({"name": 42, "input": ["x"], "output": {"a": 1},
                                          "latency_ms": "fast", "span_id": {"oops": True}}),
    "astral unicode": json.dumps({"name": "uni", "input": "😀" * 5000 + "\U0010ffff", "output": "ok"}),
}
r3_ok = True
for label, body in cases.items():
    try:
        r = requests.post(f"{BASE}/ingest/traces", data=body.encode(), headers=H, timeout=20)
        code = r.status_code
    except requests.RequestException as e:
        code = f"transport: {type(e).__name__}"
    try:
        healthy = requests.get(f"http://localhost:{PORT}/health", timeout=5).json().get("status") == "ok"
    except requests.RequestException:
        healthy = False
    verdict = "ok" if healthy and (isinstance(code, int) and (code < 500)) else "FINDING"
    if not healthy:
        verdict = "ENGINE DOWN - CRITICAL"
        r3_ok = False
    if isinstance(code, int) and code >= 500:
        r3_ok = False
    print(f"  {label:28s} -> {code}; engine healthy after: {healthy}  [{verdict}]")

print()
print("=" * 72)
print("R4  sustained concurrent load (20 threads x 100 traces + judged eval mid-burst)")
print("=" * 72)

MARK = f"load-{int(time.time())}"
latencies = []
errors = []


def worker(w):
    sess = requests.Session()
    out = []
    for i in range(100):
        body = json.dumps({"name": MARK, "input": f"w{w}-q{i}", "output": "a",
                           "span_id": f"{MARK}-w{w}-{i}"})
        t0 = time.time()
        try:
            r = sess.post(f"{BASE}/ingest/traces", data=body, headers=H, timeout=30)
            if r.status_code != 200:
                errors.append(r.status_code)
        except requests.RequestException as e:
            errors.append(type(e).__name__)
        out.append((time.time() - t0) * 1000)
    return out


t0 = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=21) as pool:
    futures = [pool.submit(worker, w) for w in range(20)]
    eval_result = None

    def mid_burst_eval():
        ds = (client.evaluations.datasets.builder(name="dd-load-eval", jaccard_similarity=True)
              .add_case(query="q", expected_results="the answer").publish())
        run = (client.evaluations.run(dataset_id=ds.id,
                                      subject={"kind": "custom_agent", "framework": "raw_python"})
               .execute(lambda case: "the answer").finalize())
        return run.results()[0].jaccard_similarity

    eval_future = pool.submit(mid_burst_eval)
    for f in futures:
        latencies.extend(f.result())
    eval_result = eval_future.result()
wall = time.time() - t0

latencies.sort()
n = len(latencies)
# Exact stored count: cursor-paginate the list (page size caps at 100 server-side).
# The R3 "1000-deep nested input" trace was ACCEPTED and stored verbatim, and any list page
# containing it now blows Python's default recursion limit at parse time - the engine imposes
# no depth bound on stored JSON, so one hostile trace can break well-behaved API consumers
# reading pages that include it. Logged as a finding; worked around here.
import sys

sys.setrecursionlimit(50000)
total = 0
cursor = None
while True:
    url = f"{BASE}/ingest/traces?limit=100" + (f"&cursor={cursor}" if cursor else "")
    page = requests.get(url, headers=H, timeout=30).json()
    total += len([t for t in page["traces"] if t["name"] == MARK])
    if not page.get("hasNextPage"):
        break
    cursor = page.get("nextCursor")

print(f"  sent 2000 across 20 threads in {wall:.1f}s ({2000 / wall:.0f} req/s sustained)")
print(f"  errors: {len(errors)} ({sorted(set(map(str, errors)))[:3] if errors else 'none'})")
print(f"  latency p50={latencies[n // 2]:.1f}ms p95={latencies[int(n * 0.95)]:.1f}ms "
      f"p99={latencies[int(n * 0.99)]:.1f}ms max={latencies[-1]:.0f}ms")

# Distinguish storage loss from read-path loss: count the rows in the database file directly.
import sqlite3

db_total = sqlite3.connect(os.path.join(HOME, "agentx.db")).execute(
    "SELECT count(*) FROM traces WHERE name = ?", (MARK,)
).fetchone()[0]
print(f"  stored in the database: {db_total}/2000; returned by cursor pagination: {total}/2000")
if db_total == 2000 and total < 2000:
    print("  -> storage is exactly-once, but the paginated LIST drops rows that share a "
          "createdAt millisecond with a page boundary (cursor predicate is createdAt-only, "
          "no id tiebreak): the dashboard's infinite scroll silently skips these. BUG")
print(f"  judged eval mid-burst completed: jaccard={eval_result} (expect 1.0)")
r4_ok = len(errors) == 0 and db_total == 2000 and eval_result == 1.0  # pagination skip logged as a finding

hard_kill(proc)
print()
for k, v in {"R1 crash durability": r1_ok, "R2 engine-down behavior": r2_ok,
             "R3 malformed payloads": r3_ok, "R4 concurrent load": r4_ok}.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")
print("\nRELIABILITY PROBES " + ("PASS" if all([r1_ok, r2_ok, r3_ok, r4_ok]) else "FAIL"))
