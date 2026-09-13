# SDK eval samples

Offline evaluation through the Python SDK: datasets, runs, judge scorers, trajectory checks.
Every script needs `AGENTX_API_KEY` (the engine prints the default project key at startup) and
targets `AGENTX_SELFHOST_BASE_URL` (default `http://localhost:4700/api/v1`).

| Script | What it shows | Extra env / keys |
|---|---|---|
| `selfhost_eval.py` | Minimal end-to-end run: dataset + unified judge scorer + trace-linked results | judge key on the engine |
| `simple_eval_no_trace.py` | Smallest possible run: score final answers only, no traces linked | `OPENAI_API_KEY` |
| `openai_eval_simple.py` | Evaluating a plain OpenAI agent, results linked to real traces | `OPENAI_API_KEY` |
| `eval_dataset_creation.py` | Building a rich reusable dataset (per-case guidelines, follow-ups); no run | none beyond the core |
| `langchain_billing_dispute_eval.py` | Evaluating the multi-agent LangChain script it imports from `../sdk_trace_samples/langchain/` (same orchestrator, tools, and sub-agents; not reimplemented here) | `OPENAI_API_KEY`, `pip install agentx-python[langchain] langchain langchain-openai` |
| `langgraph_trajectory_eval.py` | Trajectory eval of a LangGraph agent: `expected_tools` matching + trajectory-aware judging | `OPENAI_API_KEY`, the langchain extra + `langgraph` |
| `prompt_registry_example.py` | Prompt registry basics: fetch the prompt at runtime, tag the run with its version | `OPENAI_API_KEY` |
| `databricks_agent_eval.py` | Driving a Databricks-hosted agent (Model Serving endpoint) through a dataset run | `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_ENDPOINT` |

Judge-graded runs also need an LLM key configured on the ENGINE (env var or Platform
Settings > LLM Providers) - the local `OPENAI_API_KEY` above is what the agent under test uses.
