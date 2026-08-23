"""
Deep dive - feature depth probes (engineer-lead evaluation, round 3).

F1  Metric math, end to end: stored jaccard/BLEU/ROUGE for engineered string pairs compared
    against an independent reimplementation of the documented algorithms. Verifies the wiring
    (case -> engine -> stored row), not just the vendor's own unit tests.
F2  Judge quality: labeled 8-case set (4 good / 4 bad), 3 identical runs. Measures separation,
    threshold accuracy, and per-case repeatability (stddev across runs).
F3  Dataset versioning: adding a case must record a version; a finished run's stored results
    must not change retroactively.
F4  Tracing depth: a 4-deep nested span tree read back with correct parentage; plus a raw
    OTLP/HTTP JSON export into the OTel endpoint.
F5  Code-scorer runtime safety: infinite loop (budget must kill it, engine must stay healthy),
    runtime exception (event with justification, no false signal).
F6  Prompt registry: create, runtime pull, version pinning.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=... python3 feature_probes.py
"""

import json
import math
import os
import re
import statistics
import time

import requests
from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4791/api/v1")
KEY = os.environ["AGENTX_API_KEY"]

boot = AgentX(api_key=KEY, base_url=BASE_URL)
project = boot.projects.create(f"deep-dive-features {int(time.time())}")
PKEY = project["apiKey"]
client = AgentX(api_key=PKEY, base_url=BASE_URL)
H = {"x-api-key": PKEY, "Content-Type": "application/json"}

SUBJECT = {"kind": "custom_agent", "framework": "raw_python"}


# --- Independent reference implementation of the engine's documented metric algorithms -------
def tok(text):
    return [t for t in re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE).split() if t]


def ref_jaccard(e, a):
    A, B = set(tok(e)), set(tok(a))
    return len(A & B) / len(A | B) if A | B else None


def ref_bleu(e, a):
    ref, cand = tok(e), tok(a)
    max_order = min(4, len(cand), len(ref))
    precisions = []
    for n in range(1, max_order + 1):
        def grams(ts):
            out = {}
            for i in range(len(ts) - n + 1):
                g = " ".join(ts[i:i + n])
                out[g] = out.get(g, 0) + 1
            return out
        rg, cg = grams(ref), grams(cand)
        matches = sum(min(c, rg.get(g, 0)) for g, c in cg.items())
        total = sum(cg.values())
        precisions.append((matches / total if total else 0) if n == 1 else (matches + 1) / (total + 1))
    if precisions[0] == 0:
        return 0.0
    log_sum = sum(math.log(p) for p in precisions) / len(precisions)
    bp = 1 if len(cand) > len(ref) else math.exp(1 - len(ref) / len(cand))
    return max(0.0, min(1.0, bp * math.exp(log_sum)))


def ref_rouge(e, a):
    ref, cand = tok(e), tok(a)
    dp = [[0] * (len(cand) + 1) for _ in range(len(ref) + 1)]
    for i in range(1, len(ref) + 1):
        for j in range(1, len(cand) + 1):
            dp[i][j] = dp[i - 1][j - 1] + 1 if ref[i - 1] == cand[j - 1] else max(dp[i - 1][j], dp[i][j - 1])
    lcs = dp[len(ref)][len(cand)]
    if lcs == 0:
        return 0.0
    r, p = lcs / len(ref), lcs / len(cand)
    return max(0.0, min(1.0, 2 * p * r / (p + r)))


print("=" * 72)
print("F1  Metric math, end to end (stored score vs independent reference)")
print("=" * 72)

PAIRS = {
    "identical": ("the quick brown fox jumps over the lazy dog",
                  "the quick brown fox jumps over the lazy dog"),
    "half overlap": ("alpha beta gamma delta", "alpha beta epsilon zeta"),
    "disjoint": ("alpha beta gamma", "delta epsilon zeta"),
    "reordered": ("returns take thirty days to process fully", "fully process days thirty take returns to"),
}
builder = client.evaluations.datasets.builder(
    name="dd-metric-math", jaccard_similarity=True, bleu_score=True, rouge_score=True,
)
for label, (expected, _) in PAIRS.items():
    builder = builder.add_case(query=label, expected_results=expected)
dataset = builder.publish()

run = (
    client.evaluations.run(dataset_id=dataset.id, subject=SUBJECT)
    .execute(lambda case: PAIRS[case.query][1])
    .finalize()
)
rows = {r.question_text or "?": r for r in run.results()}
worst = 0.0
for label, (e, a) in PAIRS.items():
    row = next((r for q, r in rows.items() if label in str(q)), None)
    got = (row.jaccard_similarity, row.bleu_score, row.rouge_score)
    want = (ref_jaccard(e, a), ref_bleu(e, a), ref_rouge(e, a))
    drift = max(abs((g or 0) - (w or 0)) for g, w in zip(got, want))
    worst = max(worst, drift)
    print(f"  {label:13s} stored j/b/r = {tuple(round(x, 4) if x is not None else None for x in got)}"
          f"  reference = {tuple(round(x, 4) if x is not None else None for x in want)}  |drift| {drift:.4f}")
print(f"  worst absolute drift across 12 scores: {worst:.4f} (expect < 0.005)")
f1_ok = worst < 0.005

print()
print("=" * 72)
print("F2  Judge quality: separation, threshold accuracy, repeatability (3 runs)")
print("=" * 72)

POLICY = {
    "return window": ("30 days from delivery, full refund on most items.",
                      "Returns are 30 days from delivery with a full refund for most items.",  # good
                      "We have a very generous returns policy, check our website for details."),  # bad
    "international shipping": ("Ships to 40+ countries; duties shown at checkout.",
                               "Yes, we ship to over 40 countries and duties are shown at checkout.",
                               "Shipping is available in some regions."),
    "cancel subscription": ("Settings > Billing > Cancel; effective at period end.",
                            "Go to Settings, then Billing, then Cancel - it takes effect at period end.",
                            "You can probably cancel somewhere in your account."),
    "vat invoice": ("Add VAT number under Billing details; invoices regenerate.",
                    "Add your VAT number under Billing details and invoices regenerate automatically.",
                    "Contact us and we may be able to help with invoices."),
}
jb = client.evaluations.datasets.builder(
    name="dd-judge-quality",
    acceptance_criteria="Concrete, matches the expected policy exactly (numbers, menu paths); actionable.",
    rejection_criteria="Vague, hedging, missing the specific number or menu path from the expected answer.",
)
for topic, (expected, _, _) in POLICY.items():
    jb = jb.add_case(query=f"{topic}?", expected_results=expected)
    jb = jb.add_case(query=f"{topic} (variant)?", expected_results=expected)
jdataset = jb.publish()


def agent(case):
    topic = next(t for t in POLICY if t in case.query)
    good, bad = POLICY[topic][1], POLICY[topic][2]
    return bad if "(variant)" in case.query else good


all_runs = []
t0 = time.time()
for i in range(3):
    r = client.evaluations.run(dataset_id=jdataset.id, subject=SUBJECT).execute(agent).finalize()
    ratings = {str(row.question_text or ""): row.rating for row in r.results()}
    all_runs.append(ratings)
wall = time.time() - t0

goods, bads, stds = [], [], []
correct = total = 0
for q in all_runs[0]:
    per_case = [runs.get(q) for runs in all_runs if runs.get(q) is not None]
    if not per_case:
        continue
    mean = sum(per_case) / len(per_case)
    stds.append(statistics.pstdev(per_case))
    is_bad = "(variant)" in q
    (bads if is_bad else goods).append(mean)
    predicted_bad = mean < 7
    correct += int(predicted_bad == is_bad)
    total += 1
print(f"  good answers: mean {sum(goods)/len(goods):.2f}  |  bad answers: mean {sum(bads)/len(bads):.2f}"
      f"  (separation {sum(goods)/len(goods) - sum(bads)/len(bads):.2f} points)")
print(f"  threshold accuracy (fail_under=7): {correct}/{total}")
print(f"  repeatability: mean per-case stddev across 3 identical runs = {statistics.mean(stds):.3f}, "
      f"max = {max(stds):.3f}")
print(f"  wall clock for 3 x 8 judged cases: {wall:.0f}s")
f2_ok = correct == total and statistics.mean(stds) <= 1.0

print()
print("=" * 72)
print("F3  Dataset versioning: mutation records a version; finished runs don't drift")
print("=" * 72)

before = {str(r.question_text): (r.rating, r.jaccard_similarity) for r in run.results()}
add = requests.post(f"{BASE_URL}/evaluate/datasets/{dataset.id}/cases", headers=H,
                    json={"case": {"source": "deep-dive-probe",
                                   "main_question": {"query": "a fifth case added later",
                                                     "expectedResults": "whatever"}}}, timeout=15)
versions = requests.get(f"{BASE_URL}/evaluate/datasets/{dataset.id}/versions", headers=H, timeout=15).json()
n_versions = len(versions if isinstance(versions, list) else versions.get("versions", []))
after = {str(r.question_text): (r.rating, r.jaccard_similarity) for r in run.results()}
print(f"  add-case status: {add.status_code}; versions recorded for dataset: {n_versions}")
print(f"  finished run's stored ratings unchanged after mutation: {before == after}")
print("  (dataset mutation is a dashboard/REST surface - no SDK update method: noted as SDK gap)")
f3_ok = add.status_code < 300 and n_versions >= 1 and before == after

print()
print("=" * 72)
print("F4  Tracing depth: 4-deep nesting + raw OTLP/HTTP ingest")
print("=" * 72)

SESSION = f"dd-depth-{int(time.time())}"
with client.tracer.trace("root", input={"q": "depth"}, session_id=SESSION, sync=True) as root:
    with client.tracer.trace("planner", session_id=SESSION) as planner:
        with client.tracer.trace("tool-search", session_id=SESSION) as search:
            with client.tracer.trace("llm-summarize", session_id=SESSION) as leaf:
                leaf.output = "leaf done"
            search.output = "search done"
        planner.output = "plan done"
    root.output = "root done"

spans = client.monitor.list_session_spans(SESSION)
by_id = {s.get("spanId"): s for s in spans if s.get("spanId")}


def depth_of(span):
    d, seen = 0, set()
    while span and span.get("parentSpanId") in by_id and span.get("spanId") not in seen:
        seen.add(span.get("spanId"))
        span = by_id[span["parentSpanId"]]
        d += 1
    return d


depths = sorted(depth_of(s) for s in spans)
print(f"  spans stored: {len(spans)}; parent-chain depths: {depths} (expect [0, 1, 2, 3])")
f4a_ok = len(spans) == 4 and depths == [0, 1, 2, 3]

import base64

now_ns = int(time.time() * 1e9)


def otlp_export(trace_id_bytes, span_root, span_child, encode):
    def enc(b):
        return encode(b)
    return {
        "resourceSpans": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "otlp-probe"}}]},
            "scopeSpans": [{
                "spans": [
                    {"traceId": enc(trace_id_bytes), "spanId": enc(span_root),
                     "name": "otlp-root", "kind": 1,
                     "startTimeUnixNano": str(now_ns - 50_000_000), "endTimeUnixNano": str(now_ns),
                     "attributes": [{"key": "input.value", "value": {"stringValue": "otlp question"}},
                                    {"key": "output.value", "value": {"stringValue": "otlp answer"}}]},
                    {"traceId": enc(trace_id_bytes), "spanId": enc(span_child),
                     "parentSpanId": enc(span_root), "name": "otlp-child", "kind": 1,
                     "startTimeUnixNano": str(now_ns - 40_000_000),
                     "endTimeUnixNano": str(now_ns - 10_000_000)},
                ]
            }],
        }]
    }


TID = os.urandom(16)
ROOT, CHILD = os.urandom(8), os.urandom(8)

# (a) base64-encoded ids: what a protobuf-object-faithful JSON mapping produces.
b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
r_b64 = requests.post(f"{BASE_URL}/otel/v1/traces", headers=H,
                      json=otlp_export(TID, ROOT, CHILD, b64), timeout=15)
time.sleep(1.0)
spans_b64 = client.monitor.list_session_spans(TID.hex())
linked_b64 = any(s.get("parentSpanId") for s in spans_b64)

# (b) hex-encoded ids: what the OTLP/JSON spec actually mandates for trace/span ids (and what
# opentelemetry-js's JSON exporter sends). A backend reading them as base64 garbles the ids.
TID2 = os.urandom(16)
hexe = lambda b: b.hex()  # noqa: E731
r_hex = requests.post(f"{BASE_URL}/otel/v1/traces", headers=H,
                      json=otlp_export(TID2, os.urandom(8), os.urandom(8), hexe), timeout=15)
time.sleep(1.0)
spans_hex = client.monitor.list_session_spans(TID2.hex())

print(f"  base64 ids: status {r_b64.status_code}, spans under hex session: {len(spans_b64)}, "
      f"child link preserved: {linked_b64}")
print(f"  hex ids (OTLP/JSON spec, opentelemetry-js): status {r_hex.status_code}, spans under "
      f"their real session id: {len(spans_hex)} (0 = ids were base64-misdecoded into garbage: BUG)")
f4b_ok = r_b64.status_code < 300 and len(spans_b64) == 2 and linked_b64  # hex handling logged as a finding

print()
print("=" * 72)
print("F5  Code-scorer runtime safety")
print("=" * 72)

t0 = time.time()
loop = client.monitor.scorers.dry_run(kind="code", language="python",
                                      script="async def handler(input, output, expected, metadata, trace):\n"
                                             "    while True:\n        pass\n")
t_loop = time.time() - t0
health = requests.get(BASE_URL.replace("/api/v1", "/health"), timeout=5).json()
print(f"  infinite loop: returned in {t_loop:.1f}s (budget must cap it), ok={loop.get('ok')}, "
      f"error={'timed out' in str(loop.get('error', '')).lower() or 'timeout' in str(loop.get('error', '')).lower()}")
print(f"  engine healthy afterwards: {health.get('status') == 'ok'}")

scorer = client.monitor.scorers.create_code(
    name="dd-crasher", language="python", sample_rate=1, alert_below=0.5,
    script="async def handler(input, output, expected, metadata, trace):\n    raise ValueError('scorer exploded')\n",
)
with client.tracer.trace("dd-agent", input={"q": "crash probe"}, sync=True) as s:
    s.output = "any response"
time.sleep(2.5)
events = client.monitor.scorers.events(scorer["_id"], window="24h")
signals = [x for x in client.monitor.signals.list(limit=50) if x.pattern_key.startswith("custom-eval:")]
print(f"  crashing scorer: no false signal raised: {len(signals) == 0} (expect True)")
print(f"  crash visible in the scorer's event history: {len(events) >= 1} "
      f"(docs promise 'failures log an event so the history shows them'; the error event IS "
      f"written with matched=null but the history read filters matched!=null - if False, the "
      f"operator has no UI/SDK-visible way to learn their scorer is crashing: BUG)")
f5_ok = t_loop < 15 and health.get("status") == "ok" and len(signals) == 0  # invisibility logged as a finding

print()
print("=" * 72)
print("F6  Prompt registry: create, runtime pull, version pinning")
print("=" * 72)

prompt = client.evaluations.prompts.create("dd-system-prompt", "You are a terse support agent. v1.")
pulled = client.evaluations.prompts.get("dd-system-prompt")
pinned = client.evaluations.prompts.get("dd-system-prompt", version=1)
print(f"  created id={prompt.id} v{prompt.version}; pulled by name: v{pulled.version} "
      f"text-match={pulled.text == prompt.text}; pinned get(version=1) works: {pinned.version == 1}")
print("  (publishing a NEW version is dashboard-approval-only by design - config-as-code can "
      "create but not silently rewrite a live prompt)")
f6_ok = pulled.text == prompt.text and pinned.version == 1

print()
verdicts = {"F1 metric math": f1_ok, "F2 judge quality": f2_ok, "F3 versioning": f3_ok,
            "F4 tracing depth+OTLP": f4a_ok and f4b_ok, "F5 scorer runtime safety": f5_ok,
            "F6 prompt registry": f6_ok}
for k, v in verdicts.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")
print("\nFEATURE PROBES " + ("PASS" if all(verdicts.values()) else "FAIL"))
