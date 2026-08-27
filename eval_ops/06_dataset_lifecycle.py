"""
06 - Dataset lifecycle: export a golden dataset, import it as a copy, delete cleanly.

Datasets are the asset your eval program accumulates - this verifies they can be moved between
projects/instances (import is always a COPY with a fresh id, never a restore-in-place) and
retired without leaving orphans (delete removes the dataset, its grading config, and version
history; past runs are deliberately kept as history).

Run: AGENTX_API_KEY=... AGENTX_SELFHOST_BASE_URL=http://localhost:4700/api/v1 python3 06_dataset_lifecycle.py
No judge calls - fully deterministic.
"""

import os
import sys
import time

from dotenv import load_dotenv
from agentx import AgentX

load_dotenv()
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
bootstrap = AgentX(api_key=os.environ.get("AGENTX_API_KEY", ""), base_url=BASE_URL)
project_a = bootstrap.projects.create(f"Eval ops 06a {int(time.time())}")
project_b = bootstrap.projects.create(f"Eval ops 06b {int(time.time())}")
client_a = AgentX(api_key=project_a["apiKey"], base_url=BASE_URL)
client_b = AgentX(api_key=project_b["apiKey"], base_url=BASE_URL)
client_a.ping()

failures = []
def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'BAD'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)

# --- 1. A golden dataset with the full case anatomy ------------------------------------------
original = (
    client_a.evaluations.datasets.builder(
        name="Golden support set",
        evaluation_criteria="Must be concrete and correct.",
        jaccard_similarity=True,
    )
    .add_case(
        query="How long is the return window?",
        expected_results="30 days.",
        expected_tools=["lookup_policy"],
        splits=["smoke"],
    )
    .add_case(query="Do you ship internationally?", expected_results="Yes, to 40 countries.")
    .publish()
)

# --- 2. Export/import: the same cases land in ANOTHER project as a fresh copy ----------------
exported = client_a.evaluations.datasets.get(original.id)
copy = client_b.evaluations.datasets.import_dataset(exported, name="Golden support set (staging)")

check("the copy has a fresh id (import is a copy, never a restore-in-place)", copy.id != original.id)
check("all cases survived the round trip", len(copy.questions) == 2)
mq = copy.questions[0].main_question
check("case anatomy survived: expected results, trajectory, splits",
      mq.expected_results == "30 days." and (mq.splits or []) == ["smoke"],
      f"splits={mq.splits}")
check("the copy is runnable in its new project",
      client_b.evaluations.datasets.get(copy.id).name == "Golden support set (staging)")

# --- 3. Delete: the retired dataset goes away completely, runs stay --------------------------
run = client_a.evaluations.run(original.id, {"displayName": "policy-bot"})
run.execute(lambda case: "30 days." if "return" in case.query else "Yes, to 40 countries.").finalize()

client_a.evaluations.datasets.delete(original.id)
still_listed = [d.id for d in client_a.evaluations.datasets.list()]
check("the dataset is gone from the catalog", original.id not in still_listed)

kept = client_a.evaluations.get_run(run.run_id)
check("the past run is KEPT as history (its rows and ratings survive)",
      kept.get("resultCount") == 2, f"resultCount={kept.get('resultCount')}")

try:
    client_a.evaluations.datasets.delete(original.id)
    double_delete_404 = False
except Exception:
    double_delete_404 = True
check("deleting twice is an error, not a silent no-op", double_delete_404)

if failures:
    print(f"\nFAILED: {failures}")
    sys.exit(1)
print("\nDataset lifecycle verified: portable as a copy, deletable without orphans, history preserved.")
