# Eval deep dive

Six scripts that exercise AgentX's evaluation surface - offline and online - and **assert** the
behaviour rather than demoing it. Every script creates its own project, prints one `OK`/`BAD`
line per claim, and exits non-zero on any failure, so the whole folder doubles as an acceptance
suite for the eval features. `run_all.sh` runs them in order.

The engine needs a judge key (`OPENAI_API_KEY`) for 02, 04, 05 and 06. Scripts 01 and 03 are
deliberately judge-free: everything they check is deterministic and works on an engine with no
LLM key at all.

| Script | What it proves |
| --- | --- |
| `01_offline_lifecycle.py` | Dataset -> run -> typed rows -> CI gate. The gate fails the bad version and passes the fix (incl. `no_regression` vs the previous run); similarity metrics + a JS code scorer produce numbers per row; recorded gates appear in CI history with their caller. |
| `02_grading_modes_and_analysis.py` | One rubric, two modes chosen by the data: a case with `expected_results` is graded against it as ground truth (a fluent-but-wrong answer scores 0 and the justification cites the contradiction, in English); a case without one is graded reference-free on criteria. `.analyze()` produces the written report, with the failure named in `low_scoring_cases`. |
| `03_agent_and_rag_checks.py` | The deterministic agent/RAG scorers: trajectory match (strict fails right-calls-wrong-order, unordered passes it) against the linked trace's real tool calls, and context match (token Jaccard) that scores the RETRIEVER - a wrong chunk scores ~0 even when the final answer is right. Zero judge calls. |
| `04_pairwise_and_pytest.py` | Head-to-head judging between two runs (`both_orders`, flip rate measured, presentation alternated) and the two pytest claims a merge gate wants: `assert_evaluation` (the floor) and `assert_pairwise` (the comparison) - each shown passing what it should and failing what it should. |
| `05_online_scoring.py` | The same scorer entity live on traffic: verdicts with reasoning per trace, a below-threshold score raising exactly one Signal, and a sparse pause that stops spend without touching the rubric. |
| `06_judge_calibration_loop.py` | Is the judge itself right? Ops outcomes and user votes reported against judged traces; project calibration returns compared/agreement/false-negative rates, and a polished-but-wrong reply the judge believed surfaces as a false negative instead of disappearing. |

## What is deliberately not here

- **Judge tuning** (propose -> validate -> publish): heavier judge spend than a sample should
  make on every run; `selfhost_demo/11_feedback_calibration_and_judge_tuning.py` covers it.
- **The unified judge scorer entity itself**: `selfhost_demo/15_unified_judge_scorer.py`.
- **The human review queue** (labels on unflagged traffic): the SDK covers it as
  `client.monitor.review_queue` (`list`/`queue`/`label`/`dismiss`), but no script here exercises
  it - the dashboard's Review tab is the natural surface for hand-labelling.
