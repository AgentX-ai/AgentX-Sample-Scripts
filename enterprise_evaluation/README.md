# Enterprise Evaluation of the AgentX Eval Framework

A procurement-grade assessment conducted from the position of an enterprise tech lead choosing
an evaluation/observability framework for production AI agents. Every claim in
[REPORT.md](./REPORT.md) is backed by a runnable script in this folder and raw output captured
under `results/`.

## Buyer scenario

The evaluating company runs two production AI systems:

1. A **customer-support agent** (tool-calling loop: order lookup, refund policy, escalation).
2. An **internal RAG assistant** answering policy questions over a document base.

Both ship weekly. The team needs: pre-deployment regression gates in CI, continuous quality
scoring of live traffic without runaway LLM spend, triage workflows for failures, ground-truth
feedback loops, and self-hosting (data cannot leave the VPC).

## Method

- Fresh engine instance per run (`AGENTX_HOME` pointed at a scratch dir), port 4791, with an
  OpenAI key configured engine-side so judge features are exercised for real.
- Each use case is one script. Scripts are pure SDK (`agentx` Python package) except where the
  point under test is the REST/ops surface itself.
- Raw outputs land in `results/ucN_*.txt` via the run harness. Findings (good and bad) were
  logged as they occurred in [FINDINGS.md](./FINDINGS.md), then rolled up into the report.

## Reproduce

```bash
# 1. Boot a scratch engine (from the AgentX-trace-eval repo):
cd AgentX-trace-eval/engine
PORT=4791 AGENTX_HOME=/tmp/agentx-buyer OPENAI_API_KEY=sk-... npx tsx src/index.ts
#    (or: docker run -p 4791:4700 -e OPENAI_API_KEY=sk-... agentx-selfhost)

# 2. Point the scripts at it:
export AGENTX_SELFHOST_BASE_URL=http://localhost:4791/api/v1
export AGENTX_API_KEY=<the "Default project API key" from the engine log>

# 3. Run everything:
./run_all.sh          # runs UC1..UC6 in order, tee-ing into results/
bash uc7_operations.sh  # ops probes (auth modes, isolation, limits) - curl-based on purpose
```

## Files

| File | Use case |
|---|---|
| `uc1_instrument_support_agent.py` | Instrumentation: span trees, tool calls, sessions, tokens; time-to-first-trace KPI |
| `uc2_offline_quality_gate.py` | Offline eval: dataset + judge + similarity + code scorer, CI gate semantics, determinism |
| `uc3_online_scorers.py` | Online eval: template scorers, code scorer on live traffic, signals, KPIs |
| `uc4_ground_truth_calibration.py` | Feedback + outcomes vs judge verdicts; calibration math |
| `uc5_rag_faithfulness.py` | RAG: retrieval spans, expected-context Jaccard, faithfulness judge |
| `uc6_session_multiturn.py` | Session-scoped evaluation of multi-turn conversations |
| `uc7_operations.sh` | Ops surface: auth modes, project isolation, rate limits, ingest throughput |
| `FINDINGS.md` | Raw chronological findings log (bugs, gaps, friction, praise) |
| `REPORT.md` | The final buyer report: rubric, scores, comparison, recommendation |

Conducted 2026-08-22 against engine commit `2a229d6` (self-host), SDK at repo head.
