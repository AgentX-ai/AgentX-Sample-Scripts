## The agent you are supposed to fix

A deliberately imperfect LangChain support agent, plus the AgentX evaluation that finds
what is wrong with it. The sample is the loop, not the agent: score it, read the judge's
report, close one gap, score it against the same dataset again, and watch the number move.

| File | What it is |
|---|---|
| `to_fix_agent.py` | LangChain 1.x / LangGraph ReAct agent for a fictional SaaS company: vector search over a policy handbook plus two deterministic tools. Runnable on its own. |
| `run_eval.py` | Publishes an AgentX dataset, runs the agent over every case locally inside a real trace, submits the results, and calls `.analyze()` for the LLM-judge report. |

The tools are sound. The prompt is not - that is the point.

### Setup

```bash
pip install -r ../requirements.txt   # includes langchain-anthropic
```

The shared `../.env` needs `AGENTX_API_KEY`, `ANTHROPIC_API_KEY` for the agent, and
`OPENAI_API_KEY` for the handbook embeddings. Set `AGENTX_API_BASE_URL` too if you are
pointing at a local API instead of the hosted platform (for the self-host engine:
`AGENTX_API_BASE_URL=http://localhost:4700/api/v1`).

### Run the agent

```bash
python to_fix_agent.py "Account ACC-4471 wants to cancel and be refunded. How much do they get back?"
```

It prints each tool call, then the answer and token usage.

### Run the evaluation

```bash
python run_eval.py
```

7 questions × 2 runs + 2 server-generated smoke-test paraphrases = 16 agent invocations,
roughly 3 minutes. The script prints the scored report and the dashboard URL.

To re-run against the same dataset instead of publishing a new one - which is the only way
two runs are comparable:

```bash
export AGENTX_DATASET_ID=<id printed at the end of the last run>
```

### What the evaluation measures

The dataset's acceptance criteria are strict on purpose: every policy claim grounded in the
handbook and cited by doc id, account figures matching the tools exactly, under six
sentences. Two cases carry a `judge_guideline` that pins the grading to one thing - the
refund case must land on `$4,160.14`, and the seat-drop case must answer "no". The
lifetime-license case is a hallucination guard. Jaccard and ROUGE-L similarity are enabled
(both free, no embedding calls).

### The planted gaps

The v1 agent is not meant to pass. A baseline run scored **6.5/10, MEDIUM**, with a
`min 1.0 / max 8.0` spread across runs. Each gap below is a deliberate weakness with a
one-line fix, so you can close them one at a time and watch the score move.

| # | Gap | Where it shows up | Lever |
|---|---|---|---|
| 1 | Prompt never requires searching before answering | Seat-drop case scores 1-2: the agent asks for an account ID and never opens the handbook | Add "call `search_handbook` before answering any policy question" to `SYSTEM_PROMPT` |
| 2 | No citation rule | Doc ids appear on some answers, not others; the rubric expects one per claim | Require `[POL-XXX]` next to every borrowed claim |
| 3 | No tool-discipline rule for money | Refund answers drift between runs (`min 1.0 / max 8.0` spread) | "Never do refund arithmetic yourself - call `estimate_refund` and quote it" |
| 4 | No length or format cap | Emoji bullets, tables and marketing tone against a six-sentence rubric | Add the length cap and "lead with the direct answer" |
| 5 | `TOP_K = 2` | POL-SLA and POL-SEATS compete for the same two slots, so the right doc is sometimes missing | `export TO_FIX_AGENT_TOP_K=3` |
| 6 | Threshold reasoning | Reads 98.7% as *above* 99.0% and awards a 10% credit instead of 25% | Prompt it to restate the comparison before choosing a tier |

Gaps 1-4 and 6 are all edits to `SYSTEM_PROMPT` in `to_fix_agent.py`; gap 5 is an env var.

### The iteration loop

1. Change one lever.
2. Bump the version tag: `export TO_FIX_AGENT_PROMPT_VERSION=v2`.
3. Re-run against the same dataset: `AGENTX_DATASET_ID=<id> python run_eval.py`.
4. Compare the runs in the dashboard - the version tag and the git commit ride along in
   the run metadata, so each score points back at the code that produced it.

Changing one lever per run is what makes the comparison readable.
