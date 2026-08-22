# AgentX Eval Framework - Enterprise Buyer Assessment

**Role**: tech lead selecting an evaluation/observability platform for two production AI systems
(a tool-calling support agent, an internal RAG assistant), self-hosting mandatory.
**Method**: seven scripted use cases run against a fresh engine (commit `2a229d6`) with a real
judge key; every number below was measured, raw outputs in `results/`, chronological notes in
[FINDINGS.md](./FINDINGS.md).
**Date**: 2026-08-22.

## Verdict up front

**Conditional adopt.** AgentX covers the full loop we actually need - trace → gate → monitor →
ground truth → improve - with two genuinely differentiated capabilities (judge calibration
against reality; in-engine code scorers) and the best cost-safety posture of the tools we
compared (nothing scores until enabled, opt-in everywhere). The engineering quality of what
exists is high: every use case worked, and the two bugs we found are peripheral, not core-loop.

The conditions: (1) the **redaction setting must either work or disappear** - a compliance
control that silently does nothing is a failed audit waiting to happen; (2) acceptance of a
**Python-only SDK** (OTel covers tracing from other languages, but evals/gates are Python or
raw REST); (3) acceptance of a young, single-vendor OSS platform versus more battle-tested
incumbents - mitigated by self-hosting and a clean REST surface, but real.

## KPI results (all measured)

| KPI | Target | Measured | Verdict |
|---|---|---|---|
| Time-to-first-trace | < 30 min from install | **0.01 s** after client init; ~20 LOC for a 2-turn tool agent | PASS |
| CI gate correctness | healthy=0 / regressed=1 exit codes | exit 0 (avg 10.0) vs exit 1 (avg 0.5) | PASS |
| Judge repeatability | ≤ 1.5 rating-pt drift on identical runs | **0.00** points across 2 runs | PASS |
| Gated run wall-clock | < 5 min for a small suite | 6-8 s for 4 judged cases | PASS |
| Cost safety | no LLM spend without explicit opt-in | verified: key+PII trace → 0 signals until scorer enabled | PASS |
| Signal dedupe | 1 row per recurring issue | 3 identical leaks → 1 signal, occurrenceCount 3 | PASS |
| Calibration math | correct confusion matrix | TP/FP/TN/FN 1/1/1/1 → agreement .50, FP .50, FN .50 exactly | PASS |
| Session-level judging | catches cross-turn failure | context-loss conversation rated 4/10, drift span identified | PASS |
| RAG retriever gate | deterministic separation | correct retrieval 1.00 vs broken path 0.04, 0 judge calls | PASS |
| Ingest latency | p95 < 50 ms | p50 1.1 ms / p95 1.5 ms (~900 traces/s sequential, SQLite) | PASS |
| Tenant isolation | zero cross-project reads | 0 rows / 0 content leakage | PASS |
| Redaction | PII masked when enabled | **SSN stored verbatim with redactionMode=strict** | **FAIL** |

## Scored rubric

Weighted 1-5 per line; weights reflect our procurement priorities.

### Mission-critical (weight ×3)
| Criterion | Score | Evidence |
|---|---|---|
| Tracing fidelity (spans, tools, errors, tokens, sessions) | 5 | UC1: failed tool captured twice (root toolCalls + errored child span), unprompted |
| Offline eval + CI gating | 5 | UC2: one-line gate, recorded history, deterministic metrics ride along |
| Online eval with cost controls | 5 | UC3: opt-in default, per-scorer sampling, quotas, zero-LLM templates |
| Self-host completeness | 4 | Single binary/Docker, SQLite→Postgres, auth modes incl. multi-tenant; no HA guidance |
| Data isolation & auth | 4 | UC7: isolation clean, 401s correct, org quotas; no SAML/SCIM/audit log |

### Must-haves (×2)
| Criterion | Score | Evidence |
|---|---|---|
| Custom scoring logic | 5 | Code scorers (Py/JS in-engine, span access, metadata) + external endpoint w/ full-span v2 contract |
| RAG evaluation | 5 | UC5: expected-context Jaccard + faithfulness judges; deterministic option is rare among vendors |
| Multi-turn/session eval | 5 | UC6: caught what per-turn checks cannot; idle-sweep + simulation exist |
| Ground truth loop | 5 | UC4: votes + outcomes + calibration with honest FP/FN; judge tuning closes the loop |
| Triage workflow | 4 | Dedupe/statuses/archive/reopen solid; assignment/mentions absent |
| Docs accuracy | 4 | Scripts written from docs mostly worked first try; sync=True child-span caveat undocumented |

### Extra points (×1)
| Criterion | Score |
|---|---|
| Judge-vs-reality calibration as a first-class metric | 5 |
| Prompt registry + evidence-driven improvement loop | 4 |
| Conversation simulation (pre-production multi-turn) | 4 |
| Model portability / cost comparison | 4 |
| Downvote-rate + KPI strip readable via SDK (`monitor.kpis`) | 4 |

### Framework/SDK quality (×2)
| Criterion | Score | Evidence |
|---|---|---|
| SDK ergonomics (write path) | 5 | Tracer/builders/gates are genuinely pleasant; framework patches exist |
| SDK coverage (read/admin path) | **2** | No trace-by-id read, no scorer admin, no project CRUD, no offline code-scorer authoring - all REST-only |
| API design | 4 | Consistent JSON, sensible errors, versioned scorer payload (v2 kept v1 keys in place) |
| Wire/type consistency | 3 | `run.results()` returns wire-cased dicts while models elsewhere are typed snake_case |
| Test discipline of the product itself | 5 | ~470 engine tests incl. dialect matrix; our probes matched documented semantics everywhere but redaction |

**Weighted total: 4.3 / 5** (dragged by SDK read-path coverage and the redaction bug).

## What's good (specifics, not vibes)

1. **The opt-in cost posture is the best in class.** A trace containing a live API key and PII
   produced zero judge calls and zero signals until we said otherwise. Combined with per-scorer
   sample rates and org-level judge quotas, LLM spend is bounded by construction.
2. **Calibration is a real differentiator.** Every vendor sells LLM-as-judge; only AgentX ships
   "was the judge right?" as a first-class, correctly-computed metric fed by three ground-truth
   channels (user votes, ops outcomes, triage re-scores) - and closes the loop with judge tuning
   validated against the very cases the old criteria got wrong.
3. **Code scorers remove our biggest ops objection** to external evaluators: no endpoint to
   host, span-level access, metadata retained per check, and honest documentation that this is
   operator-trust code execution, not a sandbox.
4. **Failure capture is honest by default** - the failed escalation tool appeared as both a
   flagged tool call and an errored child span with zero extra instrumentation.
5. **Deterministic-first options everywhere**: Jaccard/BLEU/ROUGE beside the judge, regex/phrase
   templates beside the LLM scorers, expected-retrieval-context for RAG. You can build a
   meaningful gate that costs $0 in tokens.

## What's bad / gaps / bugs

| # | Severity | Finding |
|---|---|---|
| 1 | **High (bug)** | `redactionMode` accepts values and does nothing - SSN stored verbatim under `strict`. Compliance placebo. Fix or remove. |
| 2 | High (gap) | SDK read/admin asymmetry: scorer administration, project CRUD, trace-by-id, offline code-scorer authoring are REST-only. Config-as-code teams will script raw HTTP (our own UC3 had to). |
| 3 | Medium (gap) | Python-only SDK. JS/TS agent teams get OTel tracing but no native eval/gate client. |
| 4 | Medium (bug-ish) | `sync=True` covers only the root span; child spans race read-back (reproduced). `flush()` exists but the pitfall is undocumented. |
| 5 | Medium (gap) | No bulk export, no OTLP re-export, no audit log, no SAML/SCIM. Enterprise IT checklist items. |
| 6 | Low (friction) | Wire-cased dict results vs typed snake_case models; noisy CI output from the runner; sub-ms latencies shown as 0ms. |
| 7 | Low (note) | Code/external scorer hits don't count into failureRate (evaluator-event semantics). Defensible, but alerting built on failureRate will miss them - use the scorer's own signals. |
| 8 | Risk | Single-node engine; Postgres supported and sweeps take leases, but there is no written HA/DR guidance. |

## Vendor comparison

Scores from hands-on testing (AgentX) and prior evaluations/public docs (others).

| Capability | **AgentX** | Braintrust | LangSmith | Langfuse | Arize Phoenix |
|---|---|---|---|---|---|
| Self-host (full product, OSS) | **Yes, single binary** | Hybrid (data plane) | Enterprise tier | Yes (OSS) | Yes (OSS) |
| Online eval on live traffic | Yes, opt-in | Yes | Yes | Yes | Partial |
| Zero-LLM template scorers | **6 shipped + rules** | Heuristic lib (offline-leaning) | Yes (incl. PII/injection) | Limited | Limited |
| In-engine code scorers (online) | **Py + JS, span access** | Py/TS (strong, offline+online) | Code evaluators | Via SDK pipelines | Via SDK |
| Judge-vs-reality calibration | **First-class, 3 channels** | Human review align (partial) | Feedback correlation (manual) | Scores overlay (manual) | No |
| Session/multi-turn eval | **First-class + simulation** | Partial | Threads (partial eval) | Sessions (view-leaning) | Partial |
| RAG deterministic retrieval gate | **Yes (expected-context)** | RAG judges | RAG judges | RAGAS judges | RAG judges |
| CI gating | One-liner + history | Yes (GH action) | Yes | Via SDK | Via SDK |
| Prompt management | Registry + evidence loop | Strong | Strong | Strong | Basic |
| SDK languages | **Python only** | Py + TS | Py + TS | Py + TS | Python |
| Enterprise IT (SSO/SAML, audit, export) | **Weakest here** | Mature | Mature | Mid | Mid |
| Ecosystem maturity/community | Young | High | Highest | High (OSS) | High (OSS) |
| Cost-safety defaults | **Best (all opt-in)** | Sampling | Sampling | Sampling | n/a |

**Where AgentX wins**: cost-safe defaults, calibration, session evaluation depth, deterministic
RAG gating, single-binary self-hosting, in-engine code scorers without an endpoint.
**Where incumbents win**: TS/JS SDKs, enterprise IT checkboxes (SAML/SCIM/audit/export),
ecosystem size, managed-cloud maturity, human annotation queues at scale.

## Recommendation

Adopt for the support-agent and RAG programs, self-hosted on Postgres, **gated on**:

1. Redaction either implemented or removed from the API/UI (blocking for compliance sign-off).
2. A committed roadmap item for SDK admin/read parity (scorer CRUD, project CRUD, trace read) -
   or we maintain a thin internal REST wrapper (about a day of work; UC3/UC7 are the template).
3. Ops runbook on our side: Postgres + volume backup, `AGENTX_RATE_LIMIT_*` sizing, and the
   documented posture that scorer authorship equals code execution on the engine host.

If either JS-native eval SDK or SAML becomes a hard requirement before those land, LangSmith is
the fallback with the closest capability envelope - at the cost of self-hosting economics and
the calibration loop, which no fallback replaces.
