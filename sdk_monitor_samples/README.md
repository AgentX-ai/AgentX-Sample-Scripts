# SDK monitor samples

Monitor / Signal detection through the Python SDK. Needs `AGENTX_API_KEY` and targets
`AGENTX_SELFHOST_BASE_URL` (default `http://localhost:4700/api/v1`).

| Script | What it shows | Extra env / keys |
|---|---|---|
| `langchain_healthcare_assistant_signal.py` | A LangChain healthcare agent whose clinical-reference tool fails mid-run: the callback handler records the failed tool call, Monitor's built-in sweep turns it into a Signal, contrasted against a clean multi-tool trace | `OPENAI_API_KEY`, `pip install agentx-python[langchain] langchain langchain-openai` |

For the framework-agnostic version of the same story (custom patterns, plain OpenAI loop), see
`../selfhost_demo/06_monitor_patterns_and_signals.py`; for OK/BAD verification scripts of the
monitoring ops surface, see `../monitor_ops/`.
