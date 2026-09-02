# Monitor ops: routing, custom scorers, webhooks, topics, session judging, and spend brakes

Runnable verification scripts (OK/BAD per claim, non-zero exit on failure) for the monitoring
operations features. Each creates its own throwaway project.

| Script | Proves |
|---|---|
| `01_rules_route_traffic.py` | Automation rules route matching traffic: refund questions sampled into human review, errored traces appended as regression dataset cases; disabled rules route nothing |
| `02_custom_scorers.py` | A Python code scorer and your own HTTP endpoint score live traffic judge-free; low scores raise signals; `dry_run` tests without persisting |
| `03_webhooks.py` | Pattern failures POST to the agent profile's webhook channel the moment they fire; a recorded CI gate failure fans out too - your incident tracker hears about bad ships unprompted |
| `04_topics.py` | Project-level opt-in classification (`client.monitor.set_topics(True)`) turns raw traffic into named, counted themes readable via `client.monitor.topics()` |
| `05_session_judge.py` | Whole conversations judged as conversations: a cross-turn contradiction drags the session score down with per-step findings; session-scoped evaluators are creatable from the SDK and never judge per turn at ingest |
| `06_online_judge_cap.py` | `AGENTX_QUOTA_ONLINE_JUDGE_CALLS_PER_DAY` stops live judge spend exactly at the cap under a traffic burst, without ever blocking ingestion (self-skips unless the engine is started with the cap) |

Not covered on purpose: a per-model "sovereignty matrix" sample - the self-host engine does not
yet persist per-result model grouping (plan task #109), so there is nothing real to verify;
compare models with two version-tagged runs instead (see `selfhost_demo/07`).

## Run

```bash
export AGENTX_API_KEY=...                                    # any project key on the engine
export AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1
./run_all.sh
```

01/02/03 are judge-free; 04 spends one classification call per themed trace (4) and 05 one
session judge call. 06 needs an engine started with
`AGENTX_QUOTA_ONLINE_JUDGE_CALLS_PER_DAY=<n>` plus `AGENTX_EXPECT_ONLINE_JUDGE_CAP=<n>` in this
shell, and skips cleanly otherwise. The engine host needs `python3` on PATH for 02's code
scorer.
