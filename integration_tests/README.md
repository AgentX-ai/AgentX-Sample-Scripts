# Integration tests: Moveworks + Databricks

Runnable verification (not just demos) for the two pull-importer integrations in
`agentx.integrations`. Both vendors' APIs are **mocked** with records shaped like the real
exports, so the tests need no Moveworks or Databricks account; the AgentX engine side is real,
so ingest, dedupe, sessions, tool calls, and the LLM judge are all exercised for real.

| Script | Covers |
|---|---|
| `01_moveworks_mock_sync.py` | Data API records -> traces/sessions/tool_calls, timestampless skip, `evaluate_against` (per-interaction judge, low score for the brush-off), re-sync dedupe never re-bills, `judge_sessions` + ifStale |
| `02_databricks_mock_sync.py` | MLflow span trees -> full trees with parent links, TOOL spans mirrored to root `tool_calls` (ERROR -> `success=False`), IN_PROGRESS skip, idempotent re-sync, session judging, `evaluate_trace` on an imported root, `enable_mlflow_export` OTLP env (push path) |

Each script prints one `OK`/`BAD` line per claim and exits non-zero on any failure.

## Run

Needs an engine with an LLM judge key configured (the eval and session-judging checks make real
judge calls; everything else is deterministic).

```bash
export AGENTX_API_KEY=...                                    # any project key on the engine
export AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1
./run_all.sh          # or python3 <script> individually
```

Each script creates its own throwaway project, so nothing lands in your working projects.
