# Eval framework deep dive (round 3)

An engineering lead's product evaluation of the AgentX eval framework: features, reliability,
ease of use, developer friendliness, software quality. Different lens from the
`enterprise_evaluation/` rounds (which graded the enterprise checklist): this round grades the
product a team lives inside every day, and every claim in the report is backed by a probe in
this folder that anyone can re-run.

## Method

- Fresh engine from `main`, fresh projects per probe file, outputs captured in `results/`.
- Probes are adversarial where it matters: independent reimplementation of the scoring math,
  `kill -9` mid-traffic, hostile payloads, timestamp-collision load, spec-corner OTLP.
- Product bugs found are *documented in the probe output itself* and the probe suite encodes
  the honest verdict (a probe section FAILs when the product misbehaves, not when it is merely
  imperfect).

## Files

| File | What it tests |
|---|---|
| `dx_probes.py` | Cold-start time-to-value, error-message battery, SDK ergonomics inventory |
| `feature_probes.py` | Metric math vs reference, judge quality x3 runs, versioning, span trees + OTLP, code-scorer runtime safety, prompt registry |
| `reliability_probes.py` | kill -9 durability, engine-down SDK behavior, malformed payloads, 2000-trace concurrent load (boots its own engine) |
| `quality_evidence.sh` | Wire-casing seams, test-suite scale, compiler strictness, dependency surface, bundle weight |
| `DEEP_DIVE_REPORT.md` | The step-by-step report: scores, confirmed bugs, vendor comparison, verdict |
| `results/` | Raw probe outputs backing every number in the report |

## Reproduce

```bash
# engine on :4791 with a scratch AGENTX_HOME, then:
AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4791/api/v1 python3 dx_probes.py
AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4791/api/v1 python3 feature_probes.py
AGENTX_ENGINE_DIR=.../AgentX-trace-eval/engine python3 reliability_probes.py   # own engine, :4797
AGENTX_ENGINE_DIR=... AGENTX_SDK_DIR=... AGENTX_FRONT_DIR=... AGENTX_API_KEY=... bash quality_evidence.sh
```
