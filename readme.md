# AgentX SDK sample scripts

Runnable samples for AgentX tracing, evaluation, and monitoring using the
[AgentX Python SDK](https://github.com/AgentX-ai/AgentX-Python). Two kinds live here:
per-framework SDK integrations (LangChain, CrewAI, OpenAI Agents SDK, Anthropic, Google GenAI)
and feature-based suites that run against the self-host engine (`AgentX-trace-eval`,
`http://localhost:4700` by default).

## Setup

```bash
pip install -r requirements.txt
```

Most scripts read a shared `.env` in this directory. The common variables:

```
AGENTX_API_KEY=agtx_local_...                            # the "Default project API key" the engine prints at startup
AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1    # only if not using the default port
OPENAI_API_KEY=sk-...                                    # for anything that makes judge or model calls
```

## What's here

| Directory | What it is |
|---|---|
| `selfhost_demo/` | 17 numbered demo scripts for showing the self-host engine live: trace, evaluate, monitor, prompt registry, CI gate, sessions, and more. Start here. |
| `eval_deep_dive/` | Six assertion-style scripts (`OK`/`BAD` per claim, non-zero exit on failure) covering the offline and online evaluation surface. |
| `monitor_ops/` | Six assertion-style scripts for monitoring operations: rules, custom scorers, webhooks, topics, session judging, judge-spend caps. |
| `integration_tests/` | Verification for the Moveworks and Databricks pull importers in `agentx.integrations` (vendor APIs mocked, engine real). |
| `enterprise_evaluation/` | A procurement-grade assessment of the framework: use-case scripts `uc1`-`uc10` plus the reports they back. |
| `eval_fix_demo/` | A deliberately imperfect LangChain agent plus the eval loop that finds and measures its fixes. |
| `sdk_trace_samples/` | Per-framework tracing integrations: `langchain/`, `crewai/`, `openai_agent/`, `anthropic_agent/`, `google/`, plus a decorator sample and an OpenTelemetry sample. |
| `sdk_eval_samples/` | Evaluation basics per framework, including `prompt_registry_example.py`. |
| `sdk_monitor_samples/` | Monitoring/signal basics. |
| `sdk_rag_samples/` | RAG-specific scoring: offline context match and faithfulness, online context relevancy and faithfulness. |

Each suite with a README documents its own prerequisites and run order.

## More developer documentation

[AgentX SDK docs](https://developers.agentx.so/quickstart)
