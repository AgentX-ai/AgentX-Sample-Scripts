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

## P2 - Enterprise IT checklist · **PLANNED**
Bulk export + backup runbook · audit log · generic OIDC SSO · HA/DR guidance.
See DEVELOPMENT_PLAN.md for designs and acceptance criteria.

## P3 - Competitive bets · **PLANNED**
TypeScript SDK · human annotation queues · OTLP/SIEM streaming · polish batch.
