# Development Plan - Execution Log

Per-phase record of what shipped against [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md): the
change, where it landed, and the acceptance evidence (every acceptance criterion is a runnable
probe in this folder). Statuses: DONE / IN PROGRESS / PLANNED.

---

## P0 - Trust repairs · **DONE** (2026-08-22)

### P0.1 `sync=True` covers the whole span tree - DONE
- **Change**: `agentx/tracing/tracer.py` - a root span exiting with `sync=True` now drains the
  async child-span queue (bounded by flush's 5s budget) before its own synchronous send.
  Child-only spans keep fire-and-forget behavior. Docstring + `sdk/tracing` docs updated.
- **Acceptance (met)**: UC1 with the manual `flush()` **deleted**: `20/20 consecutive passes`.
  (First attempt showed 4/20 - root-caused to the test's own 1-second-resolution session id
  colliding across runs, not the SDK; UC1 now uses a uuid session. The fix itself was verified
  clean after that correction.)

### P0.2 Metric semantics documented - DONE
- **Change**: `monitor/scorers.mdx` gained a "What moves which metric" table (operational
  outcomes and pattern hits classify the run; judge/code/external verdicts and downvotes raise
  signals / move their own KPIs without reclassifying it); `sdk/monitor.mdx` cross-links it.
- **Acceptance (met)**: UC3's semantics comment now cites the docs section instead of
  reverse-engineering the tally.

---

## P1 - SDK parity · **DONE** (2026-08-22)

Goal was "UC3 rewritten with zero `import requests`" - achieved, and the same cleanup swept the
UC4/5/6 bootstraps.

### P1.1 `client.projects` - DONE
`agentx/projects.py`: `create(name)` (returns the isolated project incl. `apiKey`), `list()`,
`delete(id)`. Every UC script now bootstraps its own project through it.

### P1.2 Trace read surface - DONE
`agentx/traces.py`: `client.traces.get(trace_id)` (full detail incl. cost) and
`client.traces.list(limit, cursor, framework)` (paginated, newest first). Closes the
"tracer is write-only" finding.

### P1.3 Scorer administration - DONE
`agentx/monitor/scorers.py`, surfaced as `client.monitor.scorers`: `templates()`,
`enable()`/`disable()` (safe read-modify-write of the opt-in template list),
`create_code()`, `create_external()`, `update()`, `delete()`, `events()`, `dry_run()`.
UC3 and UC4 now do all scorer setup through it.

### P1.4 Offline code scorers in the dataset builder - DONE
`datasets.builder(..., code_scorers=[{"name", "code"}])` passes through to the engine's
`codeScorers` field (ids auto-generated) - the scorer is versioned with the dataset.
Documented in `evaluation/code-scorers.mdx`.

### P1.5 Typed results + quiet runner - DONE
- `run.results()` returns typed `RunResultRow` (snake_case attributes incl.
  `jaccard_similarity`, `code_scorer_results`, `trace_id`; `.raw` keeps the wire dict;
  dict-style access works one deprecation cycle with a warning). UC2/UC5 rewritten on
  attributes.
- `AGENTX_EVAL_QUIET=1` silences the progress UI (spinners included). Documented in
  `sdk/ci-cd.mdx`.
- **Acceptance (met)**: UC2's three gated runs in quiet mode emit **9 non-empty lines total**
  (target: <15 per run). One self-inflicted bug during implementation (the quiet wrapper
  recursed into itself via an over-eager regex) was caught by UC5 in the same session and
  fixed before shipping.

### Post-P1 suite state
UC1-UC6 all PASS against a fresh engine through the new surfaces. Remaining REST in the
folder is deliberate: UC7 probes the ops/REST surface itself, and UC4's one `requests` call
reads project-level calibration (per-evaluator calibration IS in the SDK; project-level noted
as a small follow-up).

---

## P2 - Enterprise IT checklist · **DONE** (2026-08-22)

### P2.1 Bulk export + backup runbook
- **Engine**: `GET /export` manifest (15 entities, live row counts) + `GET /export/:entity`
  streaming NDJSON, keyset-paginated on `id`, `?since=` incremental, project-scoped by API key.
  No blind import endpoint, deliberately - restore = replay or database-level.
- **SDK**: `client.export.manifest() / .iter(entity, since=) / .dump(dir, entities=, since=)`.
- **Docs**: `self-host/backup` runbook (dump examples, both restore paths, suggested schedule).
- **Acceptance evidence**: engine suite `exportData.integration.test.ts` (6 tests incl. the
  round trip: export a seeded project, replay into a fresh one, counts + content match);
  buyer probe UC8 PASS (results/uc8_backup_restore.txt) - 6/6 content markers, sessions
  preserved, incremental filters correct.

### P2.2 Append-only audit log
- **Engine**: `audit_events` table (both dialects); response-finish tap on the /api/v1 router
  classifying control-plane mutations (scorer/pattern/judge CRUD, settings, key regeneration,
  projects) + auth events (attempted email sniffed pre-body-parser, passwords never) + bulk
  egress reads (`export.read`). Values-free summaries: field NAMES only, plus `name`.
  Data plane excluded. Reads: `GET /admin/audit` (operator token) and org-scoped
  `GET /auth-org/audit`. No update/delete surface exists anywhere.
- **Acceptance evidence**: `audit.integration.test.ts` (6 tests: one row per lifecycle event,
  secrets never land, ingest never lands, 401 gating, 404 on every mutation verb, auth-mode
  sign-up/sign-in rows incl. failure); buyer probe UC9 PASS (results/uc9_audit_trail.txt).

### P2.3 Generic OIDC SSO
- **Engine**: `AGENTX_OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET` (+ `AGENTX_OIDC_NAME` label) wires
  better-auth's genericOAuth with OIDC discovery; `/auth/config` advertises `oidc` + `ssoLabel`.
- **Dashboard**: SSO button (label from config) posting to `sign-in/oauth2` with providerId.
- **Docs**: configuration env table + authentication.mdx; SAML/SCIM explicitly unsupported.
- **Acceptance evidence**: `oidc.integration.test.ts` (3 tests against a stub issuer: config
  surface, real discovery fetch + authorization URL, nothing advertised without the trio);
  buyer probe UC10 PASS (results/uc10_sso_surface.txt). Per the plan, one real-IdP login
  verification remains a per-release checklist item.

### P2.4 HA/DR guidance
- **Docs**: `self-host/high-availability` - reference topology, what is multi-replica-safe
  with the engine's own tests cited as evidence (sweep leases, 60-way concurrent ingest),
  the three per-process caveats (rate-limit counters!), zero-downtime upgrade order, RPO/RTO
  table tied to the P2.1 backup story.

### Also in this batch (P3.4 partial + calibration parity)
- Sub-ms latency display floor: `<1ms` everywhere (shared formatter, span timeline's `0μs`
  fixed, trace Duration stat) - unit-tested, closes UC1's last friction row.
- `client.monitor.calibration(window)` - UC4 now runs with zero `import requests`.

### Round 2 assessment
- Full re-run UC1-7 PASS on a fresh engine + three new probes UC8/9/10 PASS.
- FINDINGS.md gained a "Round 2" section (every round-1 finding re-tested with disposition);
  REPORT_ROUND2.md re-scores the rubric **4.3 -> 4.8 / 5** and upgrades the verdict from
  "conditional adopt" to "adopt". Still open, now roadmap-not-trust: TS SDK, annotation
  queues, OTLP/SIEM streaming, SAML/SCIM (explicitly out of scope).

## P3 - Competitive bets · **PLANNED**
TypeScript SDK · human annotation queues · OTLP/SIEM streaming · polish batch.
