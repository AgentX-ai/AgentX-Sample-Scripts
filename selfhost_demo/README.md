## AgentX self-host demo scripts

Numbered, standalone scripts for demoing the self-host engine (`AgentX-trace-eval`) live:
trace an agent, evaluate it against a dataset, catch failures in production traffic, and use the
prompt registry's autotune loop. Each script is runnable on its own (same convention as the rest
of `sample-scripts`), so you can jump straight to whichever one matches what you want to show.

For general SDK examples against the hosted SaaS platform (LangChain/CrewAI/OpenAI/Anthropic/
Google framework integrations), see `../sdk_trace_samples/`, `../sdk_eval_samples/`, and
`../sdk_monitor_samples/` instead; this folder is self-host only.

### Before the demo

1. **Start the engine against a fresh install**, not your regular dev database, otherwise the
   demo mixes in whatever test data you've already accumulated. Point `AGENTX_HOME` at an empty
   directory:
   ```bash
   AGENTX_HOME=/tmp/agentx-demo ./dist/agentx-server --dev
   ```
   (or `yarn dev` from `AgentX-trace-eval/engine` if running from source). `--dev` opens the
   dashboard automatically, useful for narrating alongside these scripts.

2. **Set your `.env`** (in this `sample-scripts/` directory, shared with the rest of the repo):
   ```
   OPENAI_API_KEY=sk-...
   # AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1   # only if not using the default port
   ```
   No `AGENTX_API_KEY` needed: every script fetches the local key automatically the same way the
   dashboard does (self-host has no login step). `ANTHROPIC_API_KEY` is only needed if you also
   want to demo model portability against a Claude candidate model (`07_trace_portability...`).

3. **Run `01_health_check.py` first**, always. It confirms the engine is reachable, prints the
   dashboard URL, and fails fast with a clear fix if something's not running, better to find that
   out now than mid-demo.

### Suggested run order

| Script | Shows | Talking point |
|---|---|---|
| `01_health_check.py` | Engine reachable, fresh install, seed data present | "Zero setup: one binary, no external dependencies required." |
| `02_trace_your_agent.py` | Tracing a real ReAct tool-calling loop, framework-agnostic, error capture | "Works with whatever stack you're already running, LangChain or not, tool calls included." |
| `03_evaluate_with_a_dataset.py` | Dataset-based eval of a tool-calling agent, LLM judge + similarity metrics (BLEU/ROUGE/vector/Jaccard) + a custom code scorer | "Regression testing for prompts, the same idea as a CI test suite. And if the built-in metrics don't cover it, write a few lines of JS." |
| `04_online_evaluator_production_monitoring.py` | Continuous quality scoring of live traffic (including real tool-calling turns), no dataset needed | "Not just pre-release testing, this scores what's actually happening in production." |
| `05_prompt_registry_autotune_loop.py` | Prompt-as-a-service + LLM-proposed rewrites from real evidence | "The prompt registry becomes your agent's source of truth, and improvement suggestions are grounded in your worst real examples, both from test runs and live traffic." |
| `06_monitor_patterns_and_signals.py` | Failure-pattern detection (built-in + custom), signal triage | "Catches known failure modes automatically, no need to eyeball every conversation." |
| `07_trace_portability_cost_quality.py` | Same conversation, replayed against cheaper/alternate models, cost + quality compared | "See what you'd save switching models before you actually switch." |
| `08_full_governance_story.py` | All of the above, one continuous narrative | Use this one if you only have time to run a single script live. |
| `09_agent_registration.py` | How an "agent" ends up in Overview's agent table in the first place | "No signup step for agents, either -- the name you trace under *is* its identity, everywhere." |

### Notes

- `02`/`03`/`04`/`08` run a real ReAct-style tool-calling loop for "the agent" (a plain
  `chat.completions.create(..., tools=...)` loop, `client.tracer.trace_tool_call(...)` recording
  each tool execution), not a single canned LLM call, most real agents actually look like this.
  `02_trace_your_agent.py` explains the pattern in depth; the others reuse the same shape with
  less commentary. `06`'s bad-response trace and part of `08`'s stay scripted on purpose, they're
  standing in for a broken code path a well-prompted model wouldn't reliably reproduce on its own.
- Online Evaluators are created and managed via `client.monitor.online_evaluators` (real SDK
  support: builder/get/list/update/delete/ratings/events, same shape as `client.monitor.patterns`).
  A few other things these scripts demonstrate still don't have a dedicated SDK method (the prompt
  registry's `/propose` autotune call, model portability, listing registered agents, code-based
  scorers on a grading config), so those specific calls use `requests` directly against the same
  REST API the dashboard itself
  calls. That's called out inline wherever it happens.
- **Custom Evaluators** (Governance → Monitor → Custom Evaluators) is a newer, related feature not
  currently demoed by any script here: your own HTTP endpoint gets POSTed a sample of live traffic
  and its `{matches, reason?, score?}` response decides whether a signal is raised, same shape as
  Online Evaluators but judged by your own code instead of an LLM. Dashboard/REST-only for now
  (`/agent-monitoring/custom-evaluators[/:id]`, plus a `/dry-run` endpoint for testing a URL before
  saving it) — no `client.monitor.custom_evaluators` SDK method exists yet.
- `05_prompt_registry_autotune_loop.py` goes deeper than `../sdk_eval_samples/prompt_registry_example.py`.
  That one shows the basics (register a prompt, tag a run, use `prompt.text` as your system
  prompt) and stops at "click Propose improvement in the dashboard." This one actually calls
  `/propose` end to end, and demonstrates the merged evidence feed (eval-run examples *and*
  Online Evaluator-scored production traces feeding the same rewrite).
- Self-host has no multi-tenant/workspace model, so there's no `WORKSPACE_ID` to set, unlike the
  hosted-platform samples elsewhere in this repo.
