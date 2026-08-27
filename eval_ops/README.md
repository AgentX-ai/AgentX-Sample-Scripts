# Eval ops: cost controls, trust semantics, and the human loop - verified

Runnable verification scripts (OK/BAD per claim, non-zero exit on failure) for the evaluation
operations features. Each creates its own throwaway project.

| Script | Proves |
|---|---|
| `01_splits_reuse_resume.py` | Split runs execute only tagged cases (indexes preserved), `concurrency=` pools agent calls, `reuse_outputs_from=` replays recorded outputs (judge re-scores with the current config), re-`execute()` resumes past submitted cases |
| `02_review_label_calibration.py` | A human review label (queue -> label with corrected score) becomes the judge's per-scorer calibration ground truth and its tuning evidence, sourced "review" with the reviewer's words |
| `03_eval_traffic_separation.py` | A fully instrumented offline eval (real linked traces, zero flags) leaves production KPIs, live scoring, and signals untouched |
| `04_judge_failure_trust.py` | A failing judge yields skipped rows with null ratings (never zeros), free metrics still compute, and online it raises no signal and records no rating |
| `05_case_variance.py` | `number_of_requests > 1` surfaces per-case min/max/variance (`caseStatistics`) - flakiness is measured, not averaged away |
| `06_dataset_lifecycle.py` | Export -> `import_dataset()` copies a dataset (fresh id, full case anatomy) across projects; `delete()` removes dataset + config + version history while past runs survive |
| `07_ts_ci_gate.mjs` | The TypeScript CI slice (`@agentx/eval`): init -> submit -> finalize -> `gate.assert()` -> resume, from Node with zero dependencies |

## Run

```bash
export AGENTX_API_KEY=...                                    # any project key on the engine
export AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1
./run_all.sh
```

Scripts 02/03/05 make a handful of real judge calls (an LLM key must be configured on the
engine); 04 deliberately requires that NO Anthropic key is configured (the missing key is the
judge failure under test); 01/06/07 are deterministic. `07_ts_ci_gate.mjs` needs Node 18+ and a
local build of `@agentx/eval` (AgentX-trace-eval: `yarn workspace @agentx/eval build`; point
`AGENTX_EVAL_SDK` at its `dist/index.js` if the checkout is not a sibling directory).
