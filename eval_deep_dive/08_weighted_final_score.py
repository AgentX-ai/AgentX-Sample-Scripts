"""
08 - A custom weighted final score: code scorers combining judge verdicts and metrics.

Code scorers run AFTER the judges and the deterministic metrics, so each one receives a
`scores` object ({ rating, judges, vectorSimilarity, jaccardSimilarity, bleuScore,
rougeScore }) and can blend them with custom weights - the "I want my own final score over
judge outcomes and cosine/jaccard/bleu/rouge" workflow. The parts worth proving:
- the blend scorer sees the SAME numbers the result row reports (recomputed here exactly);
- direction handling: the built-in metrics and normal judges are higher-is-better, but a
  judge that measures a BAD quality (10 = worst) contributes inverted;
- a "worst judge wins" gate over the primary plus every additional scorer.

Unlike the rest of this suite, this script runs in the DEFAULT project (whatever
AGENTX_API_KEY points at) rather than creating a throwaway one - the run shows up right on
your dashboard's Evaluate list, code-scorer columns included.

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 08_weighted_final_score.py
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
client = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
client.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

stamp = int(time.time())

# --- 1. The blend, as a code scorer on the grading scorer's offline profile -------------------
# Same shape as the dashboard's "Weighted final score" template: per-entry weight + direction,
# renormalized over whatever is actually present. Wordiness is a BAD-quality judge (10 = worst),
# so higherIsBetter: false flips its contribution.
BLEND_CODE = """
const WEIGHTS = {
  rating:            { weight: 0.4, higherIsBetter: true },
  jaccardSimilarity: { weight: 0.2, higherIsBetter: true },
  rougeScore:        { weight: 0.2, higherIsBetter: true },
  judges: {
    "Wordiness %d": { weight: 0.2, higherIsBetter: false },
  },
};
if (!scores) return { score: null, reasoning: "No scores available." };
let total = 0, weightUsed = 0;
const parts = [];
const add = (label, value, spec, scale) => {
  if (!spec || !spec.weight || value == null) return;
  const normalized = value / scale;
  total += (spec.higherIsBetter === false ? 1 - normalized : normalized) * spec.weight;
  weightUsed += spec.weight;
  parts.push(label + " " + value + (spec.higherIsBetter === false ? " (inverted)" : ""));
};
add("judge", scores.rating, WEIGHTS.rating, 10);
add("jaccard", scores.jaccardSimilarity, WEIGHTS.jaccardSimilarity, 1);
add("rouge", scores.rougeScore, WEIGHTS.rougeScore, 1);
for (const [name, spec] of Object.entries(WEIGHTS.judges)) {
  add(name, scores.judges[name], spec, 10);
}
if (weightUsed === 0) return { score: null, reasoning: "Nothing to weight." };
return { score: total / weightUsed, reasoning: parts.join(", ") };
""" % stamp

WORST_JUDGE_CODE = """
if (!scores) return { score: null };
const ratings = [scores.rating, ...Object.values(scores.judges)].filter(r => r != null);
if (ratings.length === 0) return { score: null, reasoning: "No judge verdicts." };
return { score: Math.min(...ratings) / 10, reasoning: "Worst of " + ratings.length + " judges." };
"""

quality = client.monitor.judge_scorers.builder(
    f"Blend quality {stamp}",
    acceptance_criteria="Score 9-10 for any answer that addresses the question in plain language.",
    rejection_criteria="Only reject answers that are empty or completely off-topic.",
    jaccard_similarity=True,
    rouge_score=True,
    code_scorers=[
        {"name": "Final score", "enabled": True, "code": BLEND_CODE},
        {"name": "Worst judge", "enabled": True, "code": WORST_JUDGE_CODE},
    ],
).publish()

# A judge that measures a BAD quality on purpose: 10 = unbearably wordy. The blend inverts it.
wordiness = client.monitor.judge_scorers.builder(
    f"Wordiness {stamp}",
    acceptance_criteria="Score 8-10 ONLY if the answer is extremely long-winded and padded.",
    rejection_criteria="Score 0-2 if the answer is short and direct.",
).publish()

try:
    dataset = (
        client.evaluations.datasets.builder(name=f"Weighted blend demo {stamp}")
        .add_case(query="How long is the return window?",
                  expected_results="30 days from delivery.")
        .add_case(query="Do you ship internationally?",
                  expected_results="Yes, to most countries, in 7-14 business days.")
        .publish()
    )

    ANSWERS = {
        "How long is the return window?": "30 days from delivery.",
        "Do you ship internationally?": "Yes, to most countries, in 7-14 business days.",
    }

    run = (
        client.evaluations.run(
            dataset_id=dataset.id,
            subject={"kind": "custom_agent", "displayName": "weighted-blend-demo"},
            scorer_id=quality.id,
            additional_scorer_ids=[wordiness.id],
        )
        .execute(lambda case: ANSWERS[case.query])
        .finalize()
    )

    # --- 2. The blend matches a recomputation from the row's own reported numbers -----------------
    # Judge ratings vary run to run; the CONTRACT is that the code scorer saw exactly the values the
    # row reports. Recomputing the same formula from the row and comparing pins that, noise and all.
    def expected_blend(row):
        wordiness_rating = next(
            (v["rating"] for v in (row.judge_scorer_results or []) if v["name"] == f"Wordiness {stamp}"), None
        )
        parts = [
            (row.rating, 0.4, True, 10),
            (row.jaccard_similarity, 0.2, True, 1),
            (row.rouge_score, 0.2, True, 1),
            (wordiness_rating, 0.2, False, 10),
        ]
        total = weight_used = 0.0
        for value, weight, higher_is_better, scale in parts:
            if value is None:
                continue
            normalized = value / scale
            total += (normalized if higher_is_better else 1 - normalized) * weight
            weight_used += weight
        return total / weight_used if weight_used else None

    rows = run.results()
    check("every row carries the Final score and Worst judge rows",
          all({"Final score", "Worst judge"} <= {cs["name"] for cs in (r.code_scorer_results or [])} for r in rows))

    for i, row in enumerate(rows):
        final = next(cs for cs in row.code_scorer_results if cs["name"] == "Final score")
        expected = expected_blend(row)
        ok = final["score"] is not None and expected is not None and abs(final["score"] - expected) < 1e-9
        check(f"row {i}: blend == recomputation from the row's own scores", ok,
              f"scorer={final['score']} recomputed={expected}")
        check(f"row {i}: the inverted Wordiness judge is in the reasoning",
              "(inverted)" in (final.get("reasoning") or ""), final.get("reasoning", "")[:90])

        worst = next(cs for cs in row.code_scorer_results if cs["name"] == "Worst judge")
        verdicts = [row.rating] + [v["rating"] for v in (row.judge_scorer_results or [])]
        lowest = min(v for v in verdicts if v is not None)
        check(f"row {i}: worst-judge gate == lowest verdict / 10",
              worst["score"] is not None and abs(worst["score"] - lowest / 10) < 1e-9,
              f"gate={worst['score']} lowest={lowest}")

finally:
    # Delete the demo's two judge scorers - this runs in YOUR default project, so re-runs
    # would otherwise pile up stamped Blend/Wordiness scorers on the Scorers page.
    client.monitor.judge_scorers.delete(quality.id)
    client.monitor.judge_scorers.delete(wordiness.id)

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print(f"weighted final score: all green (run {run.run_id} is on your Evaluate list)")
