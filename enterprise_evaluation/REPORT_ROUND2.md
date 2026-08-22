# Enterprise evaluation - round 2 (2026-08-22)

Second full pass by the same "enterprise tech-lead buyer", run after the vendor closed the
round-1 findings (phases P0-P2 of [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md)). Method
unchanged: every claim is a runnable probe (`uc1..uc10`, outputs in `results/`), re-run on a
fresh engine and fresh projects. [REPORT.md](./REPORT.md) is the round-1 baseline; this report
records what changed and re-scores the rubric. Raw notes: the "Round 2" section of
[FINDINGS.md](./FINDINGS.md).

## Verdict up front

**Adopt.** Round 1's verdict was "conditional adopt" with two conditions: fix the things that
lie (redaction placebo, root-only `sync=True`) and close the SDK read/admin hole. Both are
done and probe-verified, and the enterprise-IT tail (export/backup, audit trail, SSO) went
from the weakest area to a defensible one in the same release train. The remaining gaps
(TypeScript SDK, annotation queues, OTLP re-export, SAML/SCIM) are real but are now roadmap
items, not trust items - and, importantly, everything that was removed rather than fixed
(redaction) is guarded by a probe against silently returning.

## What changed since round 1, probe by probe

| Round-1 finding | Status | Round-2 evidence |
|---|---|---|
| `redactionMode` placebo (the round-1 FAIL) | Resolved by removal (round 1) | UC7.2 guard: 0 redaction fields advertised |
| `sync=True` root-only, child spans race | Fixed (P0.1) | UC1 passes with manual `flush()` deleted |
| SDK read/admin holes (scorers, projects, traces) | Fixed (P1.1-P1.3) | UC3 has zero `import requests` |
| Offline code scorers not in SDK builder | Fixed (P1.4) | UC2 `code_scorers=` kwarg |
| Wire-cased dict results, noisy CI runner | Fixed (P1.5) | UC2/UC5 typed rows; 9-line quiet CI log |
| Project calibration REST-only | Fixed | UC4 `client.monitor.calibration()` - pure SDK |
| No bulk export / backup API | **Fixed (P2.1)** | UC8: 15-entity NDJSON dump, replay-restore 6/6, incremental `since=` |
| No audit log | **Fixed (P2.2)** | UC9: honest actors, values-free summaries, immutable, egress logged |
| OAuth Google/GitHub only | **Partially fixed (P2.3)** | UC10: generic OIDC handshake real (stub-IdP discovery + authorize); SAML/SCIM stay out, documented |
| No HA/DR guidance | **Fixed (P2.4)** | `self-host/high-availability` page citing the lease + concurrency tests |
| Sub-ms latency shown as `0ms`/`0μs` | Fixed (P3.4 part) | `<1ms` floor, unit-tested |
| Metric semantics undocumented | Fixed (P0.2) | "What moves which metric" table in docs |

## KPI additions (round 2, all measured)

| KPI | Target | Measured | Verdict |
|---|---|---|---|
| Backup round trip | export -> replay -> counts+content match | 6/6 markers, session grouping preserved, manifest counts exact | PASS |
| Incremental export | `since=` filters correctly | future=0 rows, past=all rows | PASS |
| Audit completeness | scorer create/delete, key regen, sign-in each = 1 row | verified (UC9 + engine suite) | PASS |
| Audit safety | secrets never in trail; data plane excluded | planted script marker absent; 0 ingest rows | PASS |
| Audit immutability | no mutation surface | PUT/PATCH/DELETE/POST all 404 | PASS |
| SSO handshake | discovery fetched, authorize URL returned | stub-IdP URL + client_id verified; foreign callback rejected | PASS |
| No placebo controls | nothing advertised that does nothing | UC7.2 (redaction) + UC10.3 (SSO unset) both clean | PASS |

Round 1's twelve KPIs were re-run unchanged and still pass (UC1-UC7 outputs in `results/`).
The round-1 redaction FAIL row is retired: the knob no longer exists, and its absence is
probed.

## Re-scored rubric (round-1 score -> round-2 score)

### Mission-critical (weight ×3)
| Criterion | R1 | R2 | What moved it |
|---|---|---|---|
| Tracing fidelity | 5 | 5 | - |
| Offline eval + CI gating | 5 | 5 | - |
| Online eval with cost controls | 5 | 5 | - |
| Self-host completeness | 4 | **5** | Backup/export runbook + HA/DR guidance with test-backed claims |
| Data isolation & auth | 4 | **5** | Audit trail + generic OIDC; SAML/SCIM honestly scoped out |

### Must-haves (×2)
| Criterion | R1 | R2 | What moved it |
|---|---|---|---|
| Custom scoring logic | 5 | 5 | - |
| RAG evaluation | 5 | 5 | - |
| Multi-turn/session eval | 5 | 5 | - |
| Ground truth loop | 5 | 5 | - |
| Triage workflow | 4 | 4 | Assignment/mentions still absent |
| Docs accuracy | 4 | **5** | sync semantics, metric-semantics table, backup + HA pages all landed |

### Extra points (×1)
| Criterion | R1 | R2 |
|---|---|---|
| Judge-vs-reality calibration first-class | 5 | 5 |
| Prompt registry + improvement loop | 4 | 4 |
| Conversation simulation | 4 | 4 |
| Model portability / cost comparison | 4 | 4 |
| KPI strip + calibration readable via SDK | 4 | **5** |

### Framework/SDK quality (×2)
| Criterion | R1 | R2 | What moved it |
|---|---|---|---|
| SDK ergonomics (write path) | 5 | 5 | - |
| SDK coverage (read/admin path) | **2** | **5** | projects, traces, scorer admin, calibration, export dump; UC3/4/8 are zero-REST |
| API design | 4 | 4 | Export/audit surfaces consistent; REST casing conventions unchanged |
| Wire/type consistency | 3 | **4** | Typed result rows with `.raw` escape hatch; wire casing still leaks in raw dicts |
| Test discipline of the product | 5 | 5 | 483 engine tests now, incl. export round-trip, audit, OIDC-vs-stub |

**Weighted total: 4.8 / 5** (round 1: 4.3). The two structural drags from round 1 - SDK
read-path coverage and the compliance placebo - are gone; what remains is breadth (languages,
annotation tooling), not trust.

## Updated vendor position

Only the rows that moved (see REPORT.md for the full table):

| Capability | AgentX round 1 | AgentX round 2 |
|---|---|---|
| Enterprise IT (SSO, audit, export) | **Weakest area** | OIDC SSO + append-only audit + NDJSON export/backup; SAML/SCIM explicitly out |
| SDK read/admin parity | REST-only workarounds | Full parity (the config-as-code story now beats Langfuse's, matches Braintrust's) |
| HA/DR story | Undocumented | Documented with test-cited multi-replica claims |

**Still where incumbents win**: TypeScript SDK (Braintrust/LangSmith/Langfuse all have one),
human annotation queues at scale, OTLP/SIEM streaming, SAML/SCIM, ecosystem size.

## Remaining asks, in priority order

1. **TypeScript SDK** (P3.1) - the single biggest adoption blocker left for JS agent teams.
2. **OTLP/SIEM re-export** (P3.3) - audit + signals now exist and are worth streaming; today
   the workaround is polling `GET /export/events` incrementally, which works but is on us.
3. **Annotation queues** (P3.2) - the calibration loop deserves a first-class labeling UI.
4. Triage assignment/mentions - the last 4 on the rubric's must-have rows.

## Recommendation

Adopt for Python-first agent teams now; the platform's differentiators (cost-safe defaults,
calibration, session evaluation, deterministic gates) held through two adversarial passes, and
the operational objections from round 1 were closed with probes, not promises. Keep the
regression suite (`run_all.sh`, now UC1-UC10) in CI against every release; it is the contract
this vendor has - unusually - agreed to be held to.
