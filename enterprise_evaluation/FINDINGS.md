# Raw findings log

Chronological, unfiltered. Rolled up (with severity judgments) in REPORT.md.
`[BUG]` broken behavior · `[GAP]` missing capability · `[FRICTION]` works but costs time ·
`[GOOD]` notably strong · `[RISK]` works as designed but a buyer must plan around it.

## UC1 - instrumentation

- [GOOD] Time-to-first-trace: **0.01s** after client construction; the whole instrumented agent
  is ~20 lines. `with client.tracer.trace(...)` wraps anything; tool calls via context manager.
- [GOOD] Failed tool recorded twice, correctly: on the root's `toolCalls` (with `success: false`)
  AND as its own errored child span. Nothing extra required from the integrator.
- [BUG-ish/FRICTION] **`sync=True` does not cover child spans.** Root ingest is synchronous but
  tool/LLM child spans ship async; immediate read-back intermittently missed 1 of 4 spans
  (reproduced on 2nd run). `client.tracer.flush()` fixes it, but no doc warns that `sync=True`
  is root-only. An integrator wiring "trace then act on the trace" will hit this.
- [GAP] **No SDK read-back for a single trace** - `client.tracer` is write-only; there is no
  `get_trace(id)`. Session spans are readable (`client.monitor.list_session_spans`), which is
  how this script verifies, but a trace-by-id fetch requires raw REST (`GET /ingest/traces/:id`).
- [FRICTION] `latencyMs=0` for sub-millisecond spans - technically true, but a `<1ms` floor or
  microsecond field would read better in the UI for fast tools.

## UC2 - offline quality gate

- [GOOD] The whole CI story is genuinely one-liner-able: `run.gate(fail_under=7).exit_code`.
  Healthy build exit 0 (avg 10.0), regressed build exit 1 (avg 0.5). Gate verdicts are recorded
  server-side with per-check breakdowns (CI Gates history).
- [GOOD] Judge variance across two identical healthy runs: **0.00 rating points** on this
  dataset - clean separations rate stably. Wall clock ~8-13s for a 4-case judged run.
- [GOOD] Cheap deterministic metrics (Jaccard/BLEU/ROUGE/cosine) are one kwarg each on the
  dataset builder and ride alongside the judge - regressed run's jaccard 0.00 vs healthy ~0.5+.
- [GAP] Offline **code scorers are not in the SDK dataset builder** (dashboard/REST only) - the
  team's "assert on tool order" scorer can't be versioned in the repo through the SDK.
- [FRICTION] Result rows from `run.results()` are raw dicts with wire-cased keys
  (`jaccardSimilarity`), while the typed models elsewhere use snake_case properties - my first
  attempt read a non-existent `get_run_results()`/typed attrs and crashed. Ergonomics seam.
- [FRICTION] The runner prints a rich progress UI to stdout, which is lovely interactively but
  noisy in CI logs; a quiet flag would help.

## UC3 - online scorers

- [GOOD] **Opt-in posture verified**: a trace containing a live API key AND a PII email produced
  zero signals until a scorer was switched on. For an enterprise worried about surprise LLM
  spend, this is the right default, and the UI states it ("nothing is judged until...").
- [GOOD] Secrets template caught the key; signal dedupe is exactly right - 3 recurrences folded
  into 1 signal row with occurrenceCount=3.
- [GOOD] The Python code scorer deployed in one POST, hit on the apology-storm response, and its
  returned metadata ({'apologies': 3}) is retained in the event history. 13 scored checks
  recorded across all sampled traffic - full audit trail per scorer.
- [GAP] **No SDK surface for scorer administration**: enabling template scorers
  (monitoring-defaults) and code/external scorer CRUD are REST-only. The UC3 script had to drop
  to `requests`. For a team that manages config as code, this is the biggest SDK hole so far.
- [RISK/NOTE] Metric semantics: code/external scorer hits raise signals but do NOT count into
  failureRate/run outcomes (evaluator events are excluded from run classification, like judge
  scores). Defensible - but a buyer building alerting on failureRate must know a code-scorer
  hit won't move it; watch the scorer's own signals/events instead.
- [RISK] Code scorers are explicitly not sandboxed (documented). Fine for a trusted ops team,
  but scorer-creation permission == code execution on the engine host; there is no per-user
  authorization inside a project to restrict it.

## UC4 - ground truth & calibration

- [GOOD] The confusion matrix is exactly right: 4 reports (3 ops outcomes + 1 user downvote,
  which dual-writes an outcome), agreement 0.50, FP 0.50, FN 0.50 - matching the constructed
  TP/FP/TN/FN one-for-one. No other vendor in the comparison set ships judge-vs-reality
  calibration as a built-in, first-class metric.
- [GOOD] Downvote rate KPI moved (1.0 = 1 down/1 vote), the vote shows on the trace, and the
  downvote raised exactly one triage signal. Ground truth is genuinely three-channel: user
  votes, ops outcomes, and (elsewhere) human re-scores from triage.

## UC5 - RAG retriever gate

- [GOOD] `expected_retrieval_context` on a dataset case + a traced retrieval span = a
  deterministic retriever regression gate with zero judge calls: correct retrieval scored 1.0,
  the broken warranty path 0.04. Both results linked their traces (2/2) for span-level debug.
- [FRICTION] Reading per-case scorer results back requires raw REST (`GET /evaluate/:runId`) -
  `run.results()` carries them but the context-match rows sit in `codeScorerResults` with
  wire-cased keys again.

## UC6 - session evaluation

- [GOOD] The signature multi-turn failure - context loss that every individual turn hides -
  was caught by the session-level judge: 4/10 with a drift span identified, on a conversation
  whose three replies each look fine alone. Sessions are first-class (grouped, span-inspectable,
  idle-sweep judged, simulation-testable).

## UC7 - operations

- [GOOD] Project isolation airtight in the probe: zero cross-project rows, zero content leakage.
- [BUG] **`redactionMode` is a placebo.** Set to `strict`, then ingested an SSN - stored and
  returned verbatim. The setting exists in the API and the settings UI accepts it, but no
  redaction logic runs. For a buyer, a compliance setting that silently does nothing is worse
  than the setting not existing.
  - **RESOLVED (same day, by removal)**: the knob was removed from the monitoring-defaults
    surface entirely per the scope decision in DEVELOPMENT_PLAN.md (infrastructure focus; no
    placebo controls). UC7.2 now guards against its return - probe verified: 0 redaction fields
    advertised.
- [GOOD] Ingest fast enough for the target scale: p50 1.1ms / p95 1.5ms, ~900 traces/s
  sequential on SQLite; Postgres supported for real deployments.
- [GOOD] Auth-enabled mode: anonymous requests properly 401, no credential handout, multi-tenant
  org mode exists with per-org keys/quotas (validated separately in the engine's own suites).
- [GOOD] Rate limiting: credential surface 429s once the 120/min window fills (verified,
  87×429 in a 130-call burst); both ceilings env-tunable, `AGENTX_RATE_LIMIT=off` for airgapped
  setups. Correction during testing: an earlier 30/15min figure came from a stale build.
- [GAP] No bulk export / backup API: getting data OUT is paginated REST or copying the SQLite
  file / pg dump. No OTLP re-export, no audit log, no SAML/SCIM (OAuth is Google/GitHub only).
