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

---

# Round 2 (2026-08-22) - after the P0-P2 improvement phases

Same buyer, same method: every open finding above re-probed, plus three new use cases
(UC8 backup/restore, UC9 audit trail, UC10 SSO). Fresh engine, fresh projects, results in
`results/`.

## Round-1 findings, re-tested

- UC1 sync=True child-span race - **RESOLVED (P0.1)**, root `sync=True` drains the whole tree;
  UC1 passes with the manual flush deleted (20/20 in acceptance, re-verified this round).
- UC1 no SDK trace read-back - **RESOLVED (P1.2)**: `client.traces.get/list`.
- UC1 `latencyMs=0` display - **RESOLVED (P3.4 polish)**: every latency render floors to
  `<1ms` (shared formatter + timeline/duration components; the trace stat no longer shows
  "0.00 s", and the old "0μs" lie in the span timeline is gone). Unit-tested.
- UC2 code scorers not in SDK builder - **RESOLVED (P1.4)** (`code_scorers=` kwarg).
- UC2 wire-cased dict results - **RESOLVED (P1.5)**: typed rows + one-version dict shim.
- UC2 noisy CI output - **RESOLVED (P1.5)**: `AGENTX_EVAL_QUIET=1` (9-line CI log verified).
- UC3 no SDK scorer administration - **RESOLVED (P1.3)**: `client.monitor.scorers.*`; UC3 runs
  with zero `import requests`.
- UC4 project calibration REST-only - **RESOLVED**: `client.monitor.calibration(window)`; UC4
  is now pure SDK too.
- UC7 redaction placebo - **RESOLVED round 1 (by removal)**; UC7.2 still guards its return: 0
  redaction fields advertised.
- UC7 "no bulk export / backup API" - **RESOLVED (P2.1)**, see UC8 below.
- UC7 "no audit log" - **RESOLVED (P2.2)**, see UC9 below.
- UC7 "OAuth is Google/GitHub only" - **PARTIALLY RESOLVED (P2.3)**: generic OIDC covers the
  Okta/Entra/Auth0/Workspace/Keycloak class via one env trio (see UC10). SAML and SCIM remain
  unsupported - now documented explicitly rather than implied.
- UC3 code scorers unsandboxed - **UNCHANGED, accepted + documented** (trusted-operator model).

## UC8 - backup & restore drill (new)

- [GOOD] `client.export.dump(dir)` wrote 15 entities as NDJSON with a manifest whose row
  counts matched reality exactly (6 traces / 1 feedback / 2 outcomes - the 2 includes the
  downvote's dual-written outcome, consistent with UC4's round-1 finding).
- [GOOD] Replay-restore (the documented DR path) reconstructed the project in full: 6/6
  content markers, session grouping preserved, verified through a second export of the target.
- [GOOD] Incremental `since=` filters work in both directions (0 rows future, all rows past) -
  a nightly delta job is one SDK call.
- [NOTE] Replay assigns new row ids/timestamps on the target (originals stay in the exported
  file). Fine for DR, worth knowing for forensics - use the database-level path when byte-level
  history matters.

## UC9 - audit trail (new)

- [GOOD] Scorer create/delete each landed as exactly one row with an honest actor
  (`project:<id>`), entity id, and a values-free summary - the probe's `SECRET-MARKER` planted
  in the scorer script never appeared anywhere in the trail.
- [GOOD] Data-plane ingest produced zero audit rows (the trail stays readable), while bulk
  export reads are IN the trail (`export.read`) - the exact egress visibility a security team
  asks for.
- [GOOD] Gating and immutability: reads 401 without the operator token; PUT/PATCH/DELETE/POST
  on the audit surface all 404 - no mutation surface exists to abuse.
- [GOOD] Auth events (sign-up/sign-in, success AND failure, with attempted email) verified in
  the engine's own integration suite; passwords asserted never to reach the trail.

## UC10 - enterprise SSO surface (new)

- [GOOD] The `AGENTX_OIDC_*` env trio lights up a real handshake, not a painted button: the
  engine fetched the stub IdP's discovery document and returned its authorization URL with the
  right client_id. `/auth/config` advertises `oidc` + the `AGENTX_OIDC_NAME` label.
- [GOOD] No placebo: without the trio, nothing is advertised and the oauth2 route rejects the
  unknown provider.
- [GOOD] A foreign `callbackURL` is rejected (`INVALID_CALLBACK_URL`) unless the origin is in
  `AGENTX_TRUSTED_ORIGINS` - no open redirect through the SSO leg.
- [RISK] The full IdP round trip (login -> callback -> session) is CI-verified only against a
  stub's discovery+authorize leg; the plan's own rule stands: verify against one real IdP
  before each release.

## Still open after round 2

- [GAP] No OTLP/SIEM re-export of signals + audit events (P3.3) - polling the export API is
  the workaround.
- [GAP] Python-only SDK (P3.1, TypeScript planned).
- [GAP] No human annotation queues (P3.2).
- [GAP] No SAML/SCIM (explicitly out of scope; OIDC is the supported enterprise door).
