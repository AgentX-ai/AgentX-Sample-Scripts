# Development Plan - closing the gaps from the enterprise assessment

Derived line-by-line from [REPORT.md](./REPORT.md) and [FINDINGS.md](./FINDINGS.md). Each item
carries: the finding it fixes, a design sketch, size (S = hours, M = days, L = week+), and an
acceptance test. The use-case scripts in this folder are the regression suite: **a phase is done
when `run_all.sh` passes with the new expectations** - the buyer's probes become our tests.

Sequencing principle: fix trust first (things that lie), then parity (things that force
workarounds), then enterprise checklist (things that block procurement elsewhere), then
competitive bets. Within a phase, items are independent and parallelizable.

**Scope decision (2026-08-22)**: the focus is the eval framework's INFRASTRUCTURE - SDK parity,
correctness, operability - not new product features. Accordingly, redaction was resolved by
REMOVING the no-op `redactionMode` knob from the monitoring-defaults surface (a placebo
compliance control is worse than none); building real redaction is explicitly out of scope for
this plan. UC7.2 now guards against the knob's return.

---

## P0 - Trust repairs (target: this week)

Things that actively mislead an operator. (The redaction placebo - the report's #1 - is already
resolved by removal; see the scope decision above.)

### P0.1 `sync=True` must mean the whole tree, or say it doesn't (Report bug #4) - **S**
- **Problem**: root-only sync; child spans race read-back (UC1 reproduced 3/4 spans).
- **Design**: make the root span's `sync=True` exit also drain the pending child queue
  (`flush()` internally, bounded by the existing 5s timeout). It's what every caller already
  means by "sync". Keep `flush()` public for manual control. Document the semantics in
  `sdk/tracing` docs either way.
- **Acceptance**: UC1 with the manual `flush()` deleted passes 20 consecutive runs.

### P0.2 Truthful metric semantics in docs (Report note #7) - **S**
- **Problem**: code/external scorer hits don't move `failureRate`; buyers building alerts must
  learn this from source code.
- **Design**: one paragraph + table in `monitor/scorers.mdx` and `sdk/monitor.mdx` ("what counts
  into run outcomes vs. what raises signals only"), cross-linked from the KPI docs.
- **Acceptance**: docs state the exclusion; UC3's comment cites the docs section instead of
  reverse-engineering.

---

## P1 - SDK parity (target: next 2 weeks)

The single biggest scoring penalty (SDK read/admin coverage 2/5). Goal: **UC3 and UC7 rewritten
with zero `import requests`**.

### P1.1 `client.projects` - **S**
`create(name)`, `list()`, `delete(id)`, returning typed rows with `api_key`. Mirrors existing
REST; unlocks per-run isolated projects for every test suite (the pattern UC3 invented).

### P1.2 `client.tracer.get_trace(trace_id)` + `client.traces` read surface (Finding UC1-GAP) - **S**
Trace-by-id (detail incl. span tree via session linkage), plus `client.traces.list(limit, agent)`
paginated. Read-only; the wire shapes already exist on `/ingest/traces`.

### P1.3 Scorer administration (Finding UC3-GAP) - **M**
- `client.monitor.scorers.templates()` / `.enable(keys)` / `.disable(keys)` (wraps
  monitoring-defaults `enabledBuiltinPatterns` read-modify-write safely).
- `client.monitor.scorers.create_code(name, language, script, alert_below, sample_rate, ...)`,
  `.create_external(name, url, ...)`, `.update()`, `.delete()`, `.dry_run()` - full parity with
  the dashboard's custom-evaluator CRUD including `kind`.
- **Acceptance**: UC3 rewritten pure-SDK; docs' config-as-code example checked into
  `sample-scripts`.

### P1.4 Offline code scorers in the dataset builder (Finding UC2-GAP) - **S**
`builder(..., code_scorers=[{name, code}])` passing through to the existing wire field, so the
"assert on tool order" scorer lives in the repo next to the dataset it guards.

### P1.5 Typed results + quiet runner (Findings UC2-FRICTION ×2) - **M**
- `run.results()` returns typed `RunResult` objects (snake_case: `.rating`,
  `.jaccard_similarity`, `.code_scorer_results`, `.trace_id`), `.raw` keeps the wire dict.
  Deprecation shim: dict-style access keeps working one minor version with a warning.
- `execute(..., quiet=True)` / `AGENTX_EVAL_QUIET=1`: single-line-per-case output for CI logs.
- **Acceptance**: UC2/UC5 rewritten on typed rows; CI log for a 4-case run under 15 lines.

---

## P2 - Enterprise IT checklist (target: next month)

The procurement blockers where incumbents win (Report gap #5, comparison table last rows).

### P2.1 Bulk export + backup runbook - **M**
- `GET /export/:entity?since=` streaming NDJSON (traces, spans, signals, events, runs,
  feedback, outcomes), project-scoped, cursor-paginated under the hood; `client.export.dump(dir)`
  in the SDK; documented restore = replay or db-level (pg_dump / sqlite file copy) with a
  written runbook page (`self-host/backup.mdx`).
- **Acceptance**: round-trip test - export a seeded project, wipe, re-import via replay, counts
  match.

### P2.2 Audit log - **M**
- Append-only `audit_events` (actor, action, entity, before/after summary, ip, ts) written from
  a middleware tap on mutating routes + auth events. Readable via admin token
  (`GET /admin/audit`) and org-owner scoped API. No UI in v1 - the checkbox needs the trail,
  not a browser.
- **Acceptance**: creating/deleting a scorer, sign-in, key regeneration each produce one row;
  rows are immutable (no update/delete route exists).

### P2.3 Generic OIDC SSO - **M**
- better-auth already speaks generic OIDC: `AGENTX_OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET` env
  trio adds an "SSO" button; covers Okta/Entra/Google Workspace without per-vendor work. SAML
  and SCIM stay deferred (documented as such rather than implied).
- **Acceptance**: cloudOps-style integration test with a mock OIDC issuer; login flow verified
  against one real IdP before release.

### P2.4 HA/DR guidance - **S**
- A written page, not code: Postgres sizing, N engine replicas behind a load balancer (sweep
  leases already make this safe - say so with the test as evidence), what is per-process
  (rate-limit counters), zero-downtime upgrade order, RPO/RTO with the P2.1 backup story.
- **Acceptance**: page reviewed against a 2-replica docker-compose that we actually run in CI
  once.

---

## P3 - Competitive bets (target: this quarter)

Where the comparison table says incumbents win on capability, not just checklist.

### P3.1 TypeScript SDK (Report gap #3) - **L**
- Scope v1 deliberately: tracing (`trace()/span/toolCall/flush`), feedback/outcomes, and the
  CI gate (`run().execute().gate()`) - the surfaces a JS agent team needs in week one. Monitor
  admin can wait (REST parity docs cover it). Share wire-contract tests with the Python SDK via
  a golden-fixtures repo folder so the two can't drift.
- **Acceptance**: UC1 + UC2 ported to TS pass against the same engine; published to npm.

### P3.2 Human annotation queues - **L**
- The one triage capability rivals have that we lack: a reviewable queue of sampled traces with
  a rubric, assignments, and keyboard-speed scoring, writing into the existing outcome-report
  ground-truth channel (so calibration and judge tuning consume it with zero new plumbing -
  that's the differentiating twist: annotations don't just label, they tune the judges).
- **Acceptance**: annotate 20 traces in a queue; calibration's compared count reflects them;
  judge tuning lists them as evidence.

### P3.3 OTLP re-export / SIEM streaming - **M**
- Outbound: signals + audit events to a webhook batch or OTLP/HTTP endpoint (`AGENTX_EXPORT_OTLP_URL`),
  so security teams land AgentX events in Splunk/Datadog without polling.

### P3.4 Polish batch - **S**
- Sub-ms latency display floor (`<1ms`), `run_id` on gate history rows, wire-case cleanup pass
  behind the P1.5 typed layer.

---

## Regression discipline

- `enterprise_evaluation/run_all.sh` joins CI (nightly against a scratch engine): the buyer
  probes are now the contract. UC7.2 guards the removed redaction knob against returning; P0.1
  deletes UC1's manual flush; P1 items progressively delete `requests` from UC3/UC7.
- Each phase ends with a docs pass (the assessment's docs-accuracy score held at 4/5 because
  scripts written from docs mostly worked - keep that property).
- Compliance-feature work (real redaction, audit UI) stays parked until the infrastructure
  phases land; nothing on the surface may imply capabilities that do not exist in the meantime.

## Risk register

| Risk | Mitigation |
|---|---|
| Implicit child-span sync (P0.2) slows hot paths that relied on fire-and-forget | Only drain when `sync=True` was explicitly requested; async default unchanged |
| Typed-results change breaks existing scripts | One-version dict-access shim + deprecation warning; sample scripts migrated in the same PR |
| OIDC variance across IdPs | Generic OIDC only, one real-IdP verification per release, no per-vendor claims |
| TS SDK drift from Python | Shared golden wire fixtures tested by both SDKs in CI |
