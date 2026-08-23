"""
Deep dive - DX probes (engineer-lead evaluation, round 3).

D1  Cold start: wall-clock from "pip-installed SDK + api key" to (a) first trace stored and
    readable, (b) first judged eval verdict. The number a team lead actually budgets for.
D2  Error-message battery: make the mistakes a new integrator makes on day one, verbatim-log
    what the product says back, grade actionable vs cryptic.
D3  Ergonomics inventory: docstrings, keyword-only args, typed results, deprecation shims.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4791/api/v1 python3 dx_probes.py
"""

import inspect
import os
import time
import warnings

from dotenv import load_dotenv

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4791/api/v1")
KEY = os.environ["AGENTX_API_KEY"]

print("=" * 72)
print("D1  Cold start: time-to-first-value")
print("=" * 72)

t0 = time.time()
from agentx import AgentX  # noqa: E402  (import cost is part of cold start)

t_import = time.time() - t0

t0 = time.time()
boot = AgentX(api_key=KEY, base_url=BASE_URL)
project = boot.projects.create(f"deep-dive {int(time.time())}")
client = AgentX(api_key=project["apiKey"], base_url=BASE_URL)
t_setup = time.time() - t0

t0 = time.time()
with client.tracer.trace("dd-agent", input={"q": "cold start"}, sync=True) as span:
    span.output = "first answer"
first_trace = client.traces.get(span.trace_id)
t_first_trace = time.time() - t0

t0 = time.time()
dataset = (
    client.evaluations.datasets.builder(name="dd-cold-start", jaccard_similarity=True)
    .add_case(query="What is 2+2?", expected_results="4")
    .add_case(query="Capital of France?", expected_results="Paris")
    .publish()
)
run = (
    client.evaluations.run(dataset_id=dataset.id, subject={"kind": "custom_agent", "framework": "raw_python"})
    .execute(lambda case: "4" if "2+2" in case.query else "Paris")
    .finalize()
)
rows = run.results()
ratings = [r.rating for r in rows if r.rating is not None]
t_first_eval = time.time() - t0

print(f"import agentx:                 {t_import * 1000:8.0f} ms")
print(f"client + project setup:        {t_setup * 1000:8.0f} ms")
print(f"first trace stored + read:     {t_first_trace * 1000:8.0f} ms  (readable: {first_trace is not None})")
print(f"first judged eval (2 cases):   {t_first_eval:8.1f} s   (avg rating: "
      f"{sum(ratings) / len(ratings) if ratings else None})")

print()
print("=" * 72)
print("D2  Error-message battery (verbatim, graded)")
print("=" * 72)


def attempt(label, fn):
    try:
        fn()
        print(f"\n[{label}]\n  NO ERROR RAISED (finding if an error was expected)")
    except Exception as e:  # noqa: BLE001 - the message IS the subject under test
        msg = f"{type(e).__name__}: {e}"
        print(f"\n[{label}]\n  {msg[:300]}")


attempt("E1 wrong API key", lambda: AgentX(api_key="agtx_local_wrong", base_url=BASE_URL).monitor.kpis())


def probe_engine_down():
    # Constructing AgentX(base_url=...) writes os.environ["AGENTX_API_BASE_URL"], which every
    # sub-client reads back through util.api_base() - i.e. base_url is PROCESS-GLOBAL. Restore
    # it afterwards so the rest of the battery hits the real engine. The leak itself is probed
    # explicitly as E9 below.
    saved = os.environ.get("AGENTX_API_BASE_URL")
    try:
        AgentX(api_key=KEY, base_url="http://localhost:59999/api/v1").monitor.kpis()
    finally:
        if saved is not None:
            os.environ["AGENTX_API_BASE_URL"] = saved


attempt("E2 wrong port (engine down)", probe_engine_down)

attempt("E3 dataset case missing required field", lambda: client.evaluations.datasets.builder(
    name="broken"
).add_case(expected_results="no query field").publish())

attempt("E4 run on nonexistent dataset id", lambda: client.evaluations.run(
    dataset_id="does-not-exist", subject={"kind": "custom_agent", "framework": "raw_python"}
))

attempt("E5 read nonexistent trace", lambda: client.traces.get("no-such-trace-id"))

def probe_dry_run_syntax_error():
    result = client.monitor.scorers.dry_run(kind="code", language="python",
                                            script="def handler(:\n  broken")
    print(f"  (returned, not raised) {str(result)[:220]}")


attempt("E6 scorer dry-run with Python syntax error", probe_dry_run_syntax_error)


def probe_enable_unknown_scorer():
    client.monitor.scorers.enable(["totally-made-up-scorer"])
    stored = [t for t in client.monitor.scorers.templates() if t.get("enabled")]
    print(f"  (accepted without error) enabled templates now: "
          f"{[t.get('key') for t in stored]} - did the typo silently vanish or stick?")


attempt("E7 enable a template scorer that doesn't exist", probe_enable_unknown_scorer)

attempt("E8 feedback on nonexistent trace", lambda: client.feedback.report(
    "no-such-trace", "down", comment="x"
))


def probe_global_base_url():
    # E9: two clients with different base_urls cannot coexist - AgentX.__init__ writes
    # os.environ["AGENTX_API_BASE_URL"], and the surfaces that resolve util.api_base() per
    # call (traces, projects, scorers, feedback, export) follow the LAST constructor. If this
    # raises, the FIRST client broke. (monitor.kpis would NOT show it - MonitorClient captures
    # its base at construction; the SDK is inconsistent about which half it does.)
    saved = os.environ.get("AGENTX_API_BASE_URL")
    try:
        AgentX(api_key=KEY, base_url="http://localhost:59999/api/v1")  # never used again
        client.traces.get(span.trace_id)  # the ORIGINAL client, against the real engine
    finally:
        if saved is not None:
            os.environ["AGENTX_API_BASE_URL"] = saved


attempt("E9 second client with different base_url breaks the first (global-state leak)",
        probe_global_base_url)

print()
print("=" * 72)
print("D3  Ergonomics inventory")
print("=" * 72)

surfaces = {
    "tracer.trace": client.tracer.trace,
    "evaluations.run": client.evaluations.run,
    "datasets.builder": client.evaluations.datasets.builder,
    "monitor.scorers.create_code": client.monitor.scorers.create_code,
    "export.dump": getattr(getattr(client, "export", None), "dump", None),
    "feedback.report": client.feedback.report,
    "outcomes.report": client.outcomes.report,
}
doc_ok = kw_only = 0
for name, fn in surfaces.items():
    if fn is None:
        print(f"  {name}: ABSENT on this build")
        continue
    sig = inspect.signature(fn)
    has_doc = bool((inspect.getdoc(fn) or "").strip())
    kws = [p for p in sig.parameters.values() if p.kind == p.KEYWORD_ONLY]
    doc_ok += has_doc
    kw_only += bool(kws)
    print(f"  {name}: docstring={has_doc} keyword-only-params={len(kws)}")
print(f"  docstring coverage on probed surfaces: {doc_ok}/{len([f for f in surfaces.values() if f])}")

# Typed rows + the one-version dict shim: does old dict access still work, and does it warn?
row = rows[0]
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    _ = row["rating"]  # deprecated dict-style access
shim_warns = any(issubclass(w.category, DeprecationWarning) for w in caught)
print(f"  typed rows: .rating={row.rating!r} .trace_id set={bool(row.trace_id)}; "
      f"dict-access shim warns: {shim_warns}; .raw dict available: {isinstance(row.raw, dict)}")

print("\nDX probes complete")
