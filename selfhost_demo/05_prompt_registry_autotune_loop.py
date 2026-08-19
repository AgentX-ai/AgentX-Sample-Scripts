"""
The full prompt registry autotune loop, end to end: register a prompt, generate real evidence
that it's underperforming from *two* sources (a tagged eval run and Online Evaluator-scored
production traffic), ask an LLM judge to propose a rewrite grounded in that evidence, and publish
it as a new version.

For the basics (register once, use prompt.text as your agent's real system prompt, tag a run) see
../sdk_eval_samples/prompt_registry_example.py, that one stops at "click Propose improvement in
the dashboard." This one calls /propose itself and shows the merged evidence feed behind it. The
Online Evaluator below uses client.monitor.online_evaluators (real SDK support); /examples and
/propose don't have a dedicated SDK method yet (dashboard-only today), so those two calls use
`requests` directly against the same REST API the dashboard calls.

Set PUBLISH = True to actually publish the proposed rewrite as a new version at the end.
"""

import os
import time

import requests
from dotenv import load_dotenv
from openai import OpenAI
from agentx import AgentX
from agentx.evaluations.client import AgentXEvaluationsError
from agentx.evaluations.models import Dataset, EvaluationCase, Prompt
from agentx.integrations.openai import patch_openai_client

load_dotenv()

BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")
PUBLISH = False


def local_api_key() -> str:
    key = os.getenv("AGENTX_API_KEY")
    if not key:
        raise SystemExit(
            "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup."
        )
    return key


API_KEY = local_api_key()
HEADERS = {"x-api-key": API_KEY}

client = AgentX(api_key=API_KEY, base_url=BASE_URL)
oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
patch_openai_client(oai, client.tracer)

PROMPT_NAME = "demo-support-agent-prompt"

# Deliberately terse and unhelpful, so the eval run below produces real worst-rated examples for
# the judge to react to. The point of this script is showing autotune find and fix a real
# problem, not a prompt that already scores well.
WEAK_PROMPT_TEXT = "You are a support agent. Answer the user."


# --- Step 1: register the prompt (get-or-create so this script is safe to re-run) ----------------
try:
    prompt: Prompt = client.evaluations.prompts.get(PROMPT_NAME)
    print(f"Using existing prompt: {prompt.name} v{prompt.version}")
except AgentXEvaluationsError:
    prompt = client.evaluations.prompts.create(
        name=PROMPT_NAME,
        text=WEAK_PROMPT_TEXT,
        description="Demo prompt, deliberately weak so autotune has something real to fix.",
    )
    print(f"Created prompt: {prompt.name} v{prompt.version}")


def support_agent(case: EvaluationCase):
    # sync=True + returning trace_id links this result to its execution timeline in the
    # dashboard, same as 03_evaluate_with_a_dataset.py's support_agent.
    with client.tracer.trace(
        "support-agent", input={"query": case.query}, framework="openai", model="gpt-4o-mini", sync=True
    ) as span:
        resp = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt.text}, {"role": "user", "content": case.query}],
        )
        span.output = resp.choices[0].message.content
    return {
        "output": resp.choices[0].message.content,
        "metadata": {"model": resp.model},
        "trace_id": span.trace_id,
        "input_tokens": resp.usage.prompt_tokens if resp.usage else None,
        "output_tokens": resp.usage.completion_tokens if resp.usage else None,
    }


# --- Step 2: eval-run evidence -------------------------------------------------------------------
# subject.metadata.promptName + version is the tagging convention that makes the worst-rated-
# examples gathering find this run, no dedicated field, just this convention (see
# core/evaluate/prompts.ts's extractVersion). Cases chosen to expose exactly what a one-line
# system prompt gets wrong: no empathy, no policy grounding, no next step.
dataset: Dataset = (
    client.evaluations.datasets.builder(
        name="Prompt Autotune Demo Dataset",
        acceptance_criteria="Empathetic, grounded in a real policy, offers a concrete next step.",
        rejection_criteria="Curt, generic, or invents a policy that wasn't stated.",
    )
    .add_case(
        query="My package never arrived and it's been 2 weeks, I'm really frustrated.",
        expected_results="I'm really sorry to hear that, that's frustrating. Could you share your "
        "order number so I can look into it? I can offer a refund or send a replacement once I "
        "confirm what happened.",
        judge_guideline="Must acknowledge the customer's frustration before moving to logistics: "
        "a response that jumps straight to asking for information with no empathy should score "
        "no higher than 4, even if the resolution offered is otherwise correct.",
    )
    .add_case(
        query="I was charged twice for the same order, please fix this.",
        expected_results="I apologize for the duplicate charge. I've flagged this for our billing "
        "team to investigate and refund the extra charge, you should see it resolved within "
        "3-5 business days.",
        judge_guideline="Must apologize, commit to investigating/refunding, and give a concrete "
        "timeframe. Missing the timeframe caps the score around 6.",
    )
    .publish()
)
print(f"Published dataset: {dataset.id}")

client.evaluations.run(
    dataset_id=dataset.id,
    subject={
        "kind": "custom_agent",
        "displayName": "Demo Support Agent",
        "framework": "openai",
        "metadata": {"promptName": prompt.name, "version": f"{prompt.name}@v{prompt.version}"},
    },
).execute(support_agent).finalize()
print("Eval run tagged and scored.")


# --- Step 3: Online Evaluator evidence (production traffic, not a curated dataset) ---------------
settings = client.evaluations.settings.builder(
    name="Prompt Autotune Demo Production Bar",
    acceptance_criteria="Empathetic, grounded in a real policy, offers a concrete next step.",
    rejection_criteria="Curt, generic, or invents a policy that wasn't stated.",
).publish()

evaluator = client.monitor.online_evaluators.builder(
    name="Prompt Autotune Demo Evaluator",
    evaluation_settings_id=settings.id,
    sample_rate=1.0,
).publish()

# A real call against the same weak prompt, not a canned string, so this is genuinely the same
# underperforming agent as the eval-run evidence above, just hitting it as live traffic instead of
# a curated dataset. WEAK_PROMPT_TEXT has no policy grounding or empathy instruction at all, so a
# real completion against it reliably scores low on the same criteria the eval run used.
live_query = "Can I get a refund? I'm not happy."
with client.tracer.trace(
    "support-agent",
    input={"query": live_query},
    framework="openai",
    model="gpt-4o-mini",
    metadata={"promptName": prompt.name},
) as span:
    resp = oai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": prompt.text}, {"role": "user", "content": live_query}],
    )
    span.output = resp.choices[0].message.content

client.tracer.flush(timeout=10)
print(f"Sent a live trace tagged for this prompt (Online Evaluator will score it): {span.output!r}")

time.sleep(6)  # scoring runs asynchronously right after ingest


# --- Step 4: pull the merged evidence the judge will see ------------------------------------------
examples = requests.get(f"{BASE_URL}/evaluate/prompts/{prompt.id}/examples", headers=HEADERS, timeout=10).json()
eval_run_count = sum(1 for ex in examples["examples"] if ex["source"] == "eval_run")
online_eval_count = sum(1 for ex in examples["examples"] if ex["source"] == "online_evaluator")
print(f"\nEvidence gathered: {examples['exampleCount']} example(s)")
print(f"  scope: version-scoped={examples['scope']['versionScoped']}, window={examples['scope']['window']}")
print(f"  sources: {eval_run_count} eval-run, {online_eval_count} online-evaluator")
for ex in examples["examples"][:3]:
    print(f"  [{ex['source']}] rating={ex['rating']}: {ex['input'][:60]!r} -> {ex['output'][:60]!r}")


# --- Step 5: ask the judge to propose a rewrite ----------------------------------------------------
proposal = requests.post(f"{BASE_URL}/evaluate/prompts/{prompt.id}/propose", headers=HEADERS, timeout=60).json()
print(f"\nProposed rewrite (based on {proposal['sourceBreakdown']['evalRun']} eval-run + "
      f"{proposal['sourceBreakdown']['onlineEvaluator']} online-evaluator example(s)):")
print(f"\n  {proposal['revisedText']}")
print(f"\nReasoning: {proposal['reasoning']}")


# --- Step 6: publish (or don't) -------------------------------------------------------------------
if PUBLISH:
    published = requests.post(
        f"{BASE_URL}/evaluate/prompts/{prompt.id}/versions",
        headers=HEADERS,
        json={"text": proposal["revisedText"], "source": "proposed", "reasoning": proposal["reasoning"], "basedOnVersion": prompt.version},
        timeout=10,
    ).json()
    print(f"\nPublished as v{published['currentVersion']}.")
else:
    print(
        "\nPUBLISH=False, nothing was written. In the dashboard this same proposal shows as a "
        "diff the human reviews before publishing; set PUBLISH=True above to publish it "
        "programmatically instead, or use client.evaluations.prompts.get() next run to pick up "
        "whatever version is live once someone approves it via the dashboard."
    )
