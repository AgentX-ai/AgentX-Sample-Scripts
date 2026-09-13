# SDK trace samples

Runnable tracing samples for the AgentX Python SDK against a self-host engine.

## Environment

Every script reads (via `.env` or the shell):

- `AGENTX_API_KEY` - the "Default project API key" printed at engine startup (`agtx_local_...`)
- `AGENTX_SELFHOST_BASE_URL` - defaults to `http://localhost:4700/api/v1`
- A provider key for the scripts that call a real model: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY`

## Framework integrations (subdirectories)

- `anthropic_agent/` - Anthropic SDK auto-instrumentation: simple calls, tool use, RAG, prompt caching
- `crewai/` - CrewAI crews: a decorator-wrapped `crew.kickoff()` and the richer `AgentXCrewObserver` kickoff
- `google/` - Google Gemini (`google-genai`) calls and an agent loop, auto-traced
- `langchain/` - LangChain/LCEL chains and agents via `AgentXCallbackHandler`: simple, tools, RAG, tool-failure, and a multi-step billing-dispute investigation
- `openai_agent/` - OpenAI Agents SDK (`openai-agents`) traced via `AgentXTracingProcessor`: a simple run and an agent with function tools

## Loose scripts

- `deco_trace_test.py` - the `@client.tracer.trace(...)` decorator on a plain function; needs only `AGENTX_API_KEY` in `.env`
- `otel_sample.py` - no AgentX SDK at all: exports a span over OTLP/HTTP straight to the engine's `/otel/v1/traces` endpoint
- `memory_span_demo.py` - one trace mixing memory read/write spans with a knowledge retrieval, so the Execution Timeline's Memory lane has something to show
