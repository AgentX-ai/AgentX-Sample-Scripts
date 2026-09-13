# SDK RAG samples

The RAG metric pack through the Python SDK: Faithfulness and Context Relevancy (LLM judges) plus
deterministic context matching. Every script needs `AGENTX_API_KEY` and targets
`AGENTX_SELFHOST_BASE_URL` (default `http://localhost:4700/api/v1`).

| Script | What it shows | Extra env / keys |
|---|---|---|
| `rag_offline_context_match_jaccard.py` | Deterministic retriever regression check: expected vs actual retrieval context, token Jaccard. No LLM call anywhere - free to run on every retriever change | none |
| `rag_offline_faithfulness_dynamic_context.py` | Offline run where each result carries the context the agent actually retrieved; Faithfulness grades against it | judge key on the engine |
| `rag_online_faithfulness.py` | Faithfulness on live traffic: judge reads retrieved chunks straight from the trace's retrieval spans | `OPENAI_API_KEY` locally (the RAG chain) + judge key on the engine |
| `rag_online_context_relevancy.py` | Context Relevancy catching a broken retriever that Faithfulness alone would miss (generator fine, retriever broken) | `OPENAI_API_KEY` locally + judge key on the engine |

The two online scripts also need `pip install agentx-python[langchain] langchain langchain-core
langchain-openai` for the traced LangChain RAG chain.
