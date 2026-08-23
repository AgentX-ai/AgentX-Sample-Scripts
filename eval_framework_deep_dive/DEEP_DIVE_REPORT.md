# Deep-dive product report: choosing an eval framework (round 3)

*Persona: engineering lead selecting the eval framework a 10-person agent team will live in
daily. Focus: the product itself - features, reliability, ease of use, developer friendliness,
software quality. Every claim below is backed by a probe in this folder; raw outputs in
`results/`. Engine built from `main`, fresh database, 2026-08-22.*

## Verdict up front

**Choose it, with a named bug list attached to the decision.** The core product is genuinely
strong where an eval framework earns its keep: the scoring math is exactly right (0.0000 drift
against an independent reimplementation), the judge separates good from bad answers by 8.4
points with near-zero run-to-run variance, storage is exactly-once under concurrent load and
survives `kill -9`, and time-to-first-value is measured in seconds. But this round found **six
confirmed product bugs** that rounds 1 and 2 missed, and they cluster in one revealing place:
read paths. The engine writes correctly and then occasionally shows you less than the truth
(invisible scorer crashes, pagination that skips rows, OTLP ids decoded with the wrong scheme).
None are data-loss bugs; all are trust bugs. They are also all small fixes, which is why the
verdict is "choose with a bug list" rather than "wait".

Scores (1-5, evidence below): **Features 4.5 · Reliability 4.0 · Ease of use 4.5 ·
Developer friendliness 3.5 · Software quality 4.0 -> overall 4.1**.

---

## Step 1 - Ease of use: time-to-first-value (dx_probes.py, D1)

What was tested: wall-clock from an installed SDK + API key to (a) a stored, readable trace
and (b) a completed judge-scored evaluation. Measured, not estimated:

| Stage | Measured |
|---|---|
| `import agentx` | 139 ms |
| client + isolated project | 49 ms |
| first trace stored AND read back | **6 ms** |
| first judged 2-case eval, end to end | **4.0 s** (avg rating 10.0) |

The whole exercise is ~20 lines. The fluent dataset builder
(`builder(...).add_case(...).publish()`), one-call run
(`run(...).execute(fn).finalize()`), and one-call CI gate are the best-shaped write APIs I
tested in this class; they compare well with Braintrust's `Eval()` one-liner and beat
LangSmith's more ceremony-heavy client setup. **4.5/5** - the half point goes to the
`subject=` argument being required with a magic dict shape the error message doesn't explain.

## Step 2 - Ease of use: what the product says when you hold it wrong (D2)

Nine deliberate day-one mistakes, graded on the message that came back:

| Mistake | Response | Grade |
|---|---|---|
| wrong API key | `AgentXAuthError: Invalid or missing API key` | good |
| engine down | typed error after retries, but the useful fact is buried in urllib3 noise | fair |
| case missing `query` | immediate client-side `TypeError` naming the field | good |
| run on nonexistent dataset | `HTTP 404: Dataset not found` | good |
| read nonexistent trace | typed `AgentXTracesError ... Trace not found` | good |
| syntax error in code scorer | dry-run *returns* `ok: False` with the full Python traceback | very good |
| feedback on nonexistent trace | typed 404 | good |
| **enable a typo'd template scorer** | **silently accepted and stored** | **BUG (below)** |
| **second client, different base_url** | **silently breaks the first client** | **BUG (below)** |

Errors are typed per module (`AgentXAuthError`, `AgentXTracesError`, ...), which makes
`except` blocks precise. **The battery is where two of the six bugs surfaced** - see step 7.

## Step 3 - Features: is the math actually right? (feature_probes.py, F1)

The deterministic metrics (Jaccard, BLEU with Chen-Cherry smoothing, ROUGE-L) were
reimplemented independently in Python from the documented algorithms, then compared against
the engine's stored scores for engineered string pairs (identical, half-overlap, disjoint,
reordered - the reordered pair is the interesting one: Jaccard must say 1.0 while BLEU/ROUGE
punish order):

```
identical     stored (1.0, 1.0, 1.0)        reference (1.0, 1.0, 1.0)        drift 0.0000
half overlap  stored (0.3333, 0.4518, 0.5)  reference (0.3333, 0.4518, 0.5)  drift 0.0000
disjoint      stored (0.0, 0.0, 0.0)        reference (0.0, 0.0, 0.0)        drift 0.0000
reordered     stored (1.0, 0.2627, 0.2857)  reference (1.0, 0.2627, 0.2857)  drift 0.0000
```

**Worst drift across 12 scores: 0.0000.** This is the single most important table in the
report: a scoring product whose scores are exactly what it documents. (Compare: RAGAS-based
stacks have shipped metric regressions between minor versions; here the math is in one
75-line audited module with its own unit tests, and the wiring from dataset to stored row is
now verified end to end.)

## Step 4 - Features: judge quality and repeatability (F2)

8 labeled cases (4 precise policy answers, 4 vague hedging answers), 3 identical runs, 24
judge calls:

- **Separation**: good answers mean 10.00, bad answers mean 1.58 - 8.42 points apart.
- **Threshold accuracy** at the default gate (`fail_under=7`): **8/8**.
- **Repeatability**: mean per-case stddev across the 3 runs **0.059** (max 0.471) - the same
  case gets essentially the same score every time, which is what makes a CI gate on judge
  ratings tolerable at all.
- Wall clock: 45 s for 3 x 8 judged cases.

## Step 5 - Features: the rest of the surface, tested (F3-F6)

- **Dataset versioning** (F3): adding a case records a version (2 after mutation) and a
  finished run's stored ratings and similarity scores are byte-identical after the mutation.
  Runs don't drift retroactively. Gap: dataset mutation is dashboard/REST-only, no SDK method.
- **Span trees** (F4): a 4-deep nested `with tracer.trace(...)` tree stored with exact
  parentage (depths [0,1,2,3] reconstructed from parent links).
- **OTLP ingest** (F4): base64-id OTLP/JSON round-trips perfectly (2 spans, child link
  preserved). Hex-id OTLP/JSON - which is what the OTLP spec mandates for JSON and what
  opentelemetry-js sends - gets garbled (bug #3 below).
- **Code-scorer runtime** (F5): an infinite-loop scorer is killed at exactly the documented
  8 s budget with the engine healthy afterwards; a crashing scorer raises **no false signal**.
  But the crash is invisible in the scorer's history (bug #4 below).
- **Prompt registry** (F6): create, pull by name, pin by version all work; publishing a new
  version deliberately requires dashboard approval, so config-as-code can't silently rewrite
  a live prompt. Defensible, opinionated, documented.

**Features: 4.5/5.** Depth held up everywhere the probes pushed; the OTLP JSON corner is the
one real interop dent.

## Step 6 - Reliability under abuse (reliability_probes.py)

- **kill -9 durability** (R1): 50 synchronously-acknowledged traces, SIGKILL, restart on the
  same home: **50/50 survived**. The WAL claim is real.
- **Malformed payloads** (R3): 11 MB body -> 413; broken JSON -> 400; wrong types everywhere
  -> 422; NUL bytes and astral unicode -> stored without incident. Engine healthy after every
  case. No 500s, no crashes.
- **Concurrent load** (R4): 2000 traces from 20 threads in 2.3 s (**858 req/s sustained**),
  0 errors, p50 16 ms / p95 31 ms / p99 38 ms, and the database holds **exactly 2000 rows**
  (no loss, no dupes) while a judge-scored eval completed correctly mid-burst.
- **Engine-down behavior** (R2): async tracing while the backend is dead costs the
  instrumented app 2 ms total for 20 traces and raises nothing - the fire-and-forget claim is
  true for the app's latency. But `flush(timeout=3.0)` blocked for **140.8 s** (bug #5), and
  the 20 queued traces were silently dropped when the engine returned.

**Reliability: 4.0/5.** The storage engine is the trustworthy half; the SDK's outage behavior
is the half a production team must plan around today.

## Step 7 - The six confirmed bugs

All found this round, all reproduced by the probe files, all verified against source:

| # | Where | Bug | Evidence | Blast radius |
|---|---|---|---|---|
| 1 | SDK | `AgentX(base_url=...)` writes `os.environ["AGENTX_API_BASE_URL"]`; surfaces that resolve the base per call (traces, projects, scorers, feedback, export) silently follow the **last constructed client**, while `monitor` captures at construction - two clients cannot coexist and the SDK disagrees with itself about which base wins | dx E9: first client's `traces.get` hit the second client's port | Any test suite or multi-engine script; also a latent prod/staging cross-talk hazard |
| 2 | engine | `enabledBuiltinPatterns` accepts unknown keys without validation: a typo ("pii-in-respose") stores verbatim, enables nothing, reports nothing | dx E7 + settings read-back shows `["totally-made-up-scorer"]` stored | Config-as-code teams believe a scorer is on when nothing runs - the exact silent-failure class the round-1 redaction finding was about |
| 3 | engine | OTLP/**JSON** trace/span ids are decoded as base64, but the OTLP spec makes JSON ids hex (opentelemetry-js sends hex): ids garble, sessions land under junk keys, spans become unfindable | F4: hex export stored 0 spans under its real session; base64 export stored 2/2 | Every OTLP exporter configured with the JSON protocol |
| 4 | engine | Code-scorer crash events are written with `matched: null` but the history read filters `matched !== null`: **a crashing scorer is invisible** in UI and SDK (only the server console shows it) | F5 + source: `customEvaluators.ts` writes, `events.ts:726` filters | An always-crashing scorer looks identical to a scorer with nothing to report |
| 5 | SDK | `flush(timeout=3.0)` blocked 140.8 s with the backend down (per-item retry backoff multiplies the timeout); queued async traces are then dropped silently | R2 measured | CI teardown and graceful-shutdown paths hang for minutes during an outage |
| 6 | engine | Trace-list cursor pagination filters `createdAt < cursor.createdAt` with no id tiebreak: rows sharing a millisecond with a page boundary are skipped (3/2000 in a realistic burst; the database itself has all 2000) | R4: DB count 2000, paginated count 1997 | Dashboard infinite scroll and any REST consumer silently under-report bursty traffic |

Plus one robustness observation: stored JSON depth is unbounded (a 1000-deep input is
accepted), and a single such trace breaks Python consumers parsing any list page containing
it (default recursion limit). Bound it at ingest or flatten at read.

The pattern worth naming: **five of six are read-side**. Writes are validated, transactional,
and tested to 483 engine tests; reads are where the product lies slightly. A buying team
should add read-back assertions to their own smoke tests until these are fixed - and this
folder is that smoke test.

## Step 8 - Developer friendliness (D3 + Q1)

Good, with traps:

- Docstrings on 6/7 probed public surfaces; `py.typed` ships, so IDEs get real types;
  keyword-only params guard the dangerous arguments; typed result rows with a one-version
  dict-access shim that actually warns (verified).
- Quiet CI mode works (a 4-case judged run logs in 9 lines).
- The traps: bug #1 (global base_url) is the kind of thing that costs a new team an
  afternoon; wire casing is split (write side snake_case `latency_ms`, read side camelCase
  `latencyMs` - verified on one round trip in Q1); dataset mutation has no SDK surface; and
  the SDK's own test suite is thin (4 files / 53 tests) next to the engine's 65 files - which
  is precisely where bugs #1 and #5 lived.

**Developer friendliness: 3.5/5** - the write-path ergonomics are 5/5, the sharp edges above
take it down.

## Step 9 - Software quality (quality_evidence.sh)

- **Compiler posture**: engine `strict: true` plus `noUncheckedIndexedAccess: true` (rare and
  meaningful - it is why index bugs get caught at build time).
- **Test scale, run locally this round**: engine 483 passed across 65 files including a
  sqlite/postgres dialect matrix, concurrency, restart, and resilience suites; frontend 1105
  passed across 211 files; SDK 53 passed (with 2 pre-existing failures on clean `main` -
  logged, not from this round's code).
- **Dependency surface**: engine 16 runtime deps, SDK 4. For comparison, a default LangSmith
  + langchain install pulls in an order of magnitude more.
- **Bundle**: dashboard dist 13 MB, 9 MB of JS, largest chunk 2.9 MB pre-gzip - heavier than
  it should be; fine on a LAN self-host, sluggish first paint on a far edge.

**Software quality: 4.0/5.** The build discipline is real; the score caps because the six
bugs show black-box read-path coverage is missing from an otherwise strong suite, and the SDK
is under-tested relative to the product around it.

## Step 10 - Vendor comparison, product lens

Hands-on numbers for AgentX (this folder); others from prior hands-on rounds and public
docs/issue trackers. The library-class alternatives (DeepEval, promptfoo) are in scope
because an eng lead choosing "an eval framework" is often actually choosing between a
platform and a library.

| Product dimension | **AgentX** | Braintrust | LangSmith | Langfuse | Phoenix | DeepEval / promptfoo |
|---|---|---|---|---|---|---|
| Time-to-first-judged-eval | **~4 s measured** | minutes (cloud signup) | minutes | minutes (self-host compose) | minutes | seconds (library, no server) |
| Deterministic metric correctness | **verified 0.0000 drift** | good (TS/Py autoevals) | good | RAGAS-dependent | good | good but version-drift history |
| Judge repeatability (measured) | **0.059 stddev** | comparable claims, unverified | unverified | unverified | unverified | n/a (bring your own) |
| Crash durability (kill -9) | **verified 50/50** | n/a (SaaS) | n/a | depends on infra | depends | n/a |
| Sustained ingest (single node) | **858 req/s, p95 31 ms, exactly-once** | SaaS-bound | SaaS-bound | strong (ClickHouse) | strong | n/a |
| SDK offline behavior | app-safe, but flush hangs + silent drop (**bug**) | buffered, documented | buffered | buffered + retry | buffered | n/a |
| OTLP ingest | protobuf yes; **JSON-hex broken (bug)** | partial | yes | yes | **best in class** | no |
| Config-as-code safety | **typo'd scorer key silently no-ops (bug)** | validated | validated | validated | n/a | validated (schema) |
| Error messages | typed, mostly actionable | good | mixed | good | mixed | good |
| Languages | Python (TS planned) | Py+TS | Py+TS | Py+TS | Py | Py / JS |
| Self-host the whole product | **single binary** | data-plane hybrid | enterprise tier | yes | yes | n/a (no server) |

Where AgentX genuinely leads on product: measured correctness and repeatability, single-node
performance with exactly-once semantics, cost-safe defaults, and the tightest
time-to-first-value of the platform class. Where it trails: the read-side bug list above,
multi-language SDKs, and OTLP JSON interop (Phoenix is the reference implementation to match).

## Recommendation

1. **Adopt for a Python-first team**, and attach bugs #1-#6 to the vendor as the acceptance
   list for the next release; every one is small and none is architectural.
2. Until #5 is fixed, wrap `flush()` in your own timeout in CI teardown, and treat
   fire-and-forget traces during an engine outage as lost (they are).
3. Until #2 is fixed, follow every `scorers.enable()` with a `templates()` read-back
   assertion (one line; this folder's probes show how).
4. Use the OTLP protobuf protocol, not JSON, for OTel exporters until #3 is fixed.
5. Re-run this folder's probes on each engine upgrade; they are the regression contract for
   the read-side class of bug the vendor's own suite does not currently cover.

---

## Addendum (same day): all six bugs fixed and probe-verified

The vendor turned the bug table into fixes within the day; this suite's pass gates were then
TIGHTENED to the post-fix expectations (a regression in any of the six now fails the probes)
and re-run green against the fixed engine and SDK. Evidence per bug:

| # | Fix | Probe verification |
|---|---|---|
| 1 | `AgentX.__init__` no longer writes `AGENTX_API_BASE_URL` into `os.environ`; every sub-client (projects, traces, export, feedback, outcomes, scorers) now captures its base at construction, consistently | dx E9: original client keeps working after a second client with a different base is constructed; SDK unit tests assert env stays untouched and both clients' surfaces hold their own base |
| 2 | `PUT settings/monitoring-defaults` rejects unknown template-scorer keys with a 400 naming the typo and the known-key list | dx E7: `Scorer request failed (400): Unknown template scorer key(s): totally-made-up-scorer`; engine test asserts the typo is never stored |
| 3 | OTLP id decoding detects the encoding by shape (32/16-char pure hex = OTLP/JSON spec ids; otherwise base64 from the protobuf object mapping) | F4: hex-id export now stores 2/2 spans under the real session id; base64 unchanged; engine tests cover both encodings |
| 4 | The scorer-event history read no longer filters out `matched: null` rows, so a scorer's own failures appear with the error as justification | F5: crash visible in history = True, still zero false signals; engine test polls the event into visibility |
| 5 | `flush(timeout)` is a real wall-clock bound (timed condition wait instead of `queue.join()`), returns `True`/`False`, and every dropped trace now logs a WARNING with a running count | R2: `flush(timeout=3)` returned in 3.0 s (was 140.8 s). Side benefit measured: because flush no longer burns each item's full retry schedule, 19/20 traces queued during the outage were actually delivered once the engine came back, and the one genuine drop warned instead of vanishing |
| 6 | Trace-list keyset pagination gained an id tiebreak (`createdAt DESC, id DESC` with a matching cursor predicate) | R4: database 2000/2000 AND paginated read 2000/2000 (was 1997); engine test paginates 175 hand-planted timestamp collisions with zero loss and zero dupes |

Suites after the fixes: engine 492 passed (9 new regression tests), SDK 57 passed (4 new)
with only the 2 failures that pre-exist on clean `main`, frontend untouched. The
"choose with a bug list" verdict is now simply **choose**: the read-side cluster is closed,
and this folder remains the regression contract that keeps it closed.
