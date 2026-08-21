"""
Northwind Cloud support agent — the "before" agent
==================================================

A LangChain 1.x / LangGraph ReAct agent for a fictional SaaS company. It answers
billing, plan and policy questions by grounding every claim in a small handbook
(vector search) and by delegating account lookups and refund math to deterministic
tools instead of doing them in the model's head.

This is deliberately a v1: the tools are sound but the prompt is loose and retrieval
is narrow, so an evaluation has something real to find. The point of the sample is the
loop — score it, read the report, close one gap, score it again. README.md lists the
six planted gaps and the one-line fix for each.

Tools
  search_handbook(query)                      — semantic search over the policy handbook
  get_account(account_id)                     — mock account record lookup
  estimate_refund(plan, seats, days)          — deterministic prorated refund math

Install:
    pip install agentx-python langchain langchain-anthropic langchain-openai python-dotenv

Run it directly:
    python to_fix_agent.py "How do I cancel, and do I keep access until month end?"
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings

load_dotenv()

MODEL = os.getenv("TO_FIX_AGENT_MODEL", "claude-sonnet-4-6")
EMBEDDING_MODEL = os.getenv("TO_FIX_AGENT_EMBEDDING_MODEL", "text-embedding-3-small")

# Bump TO_FIX_AGENT_PROMPT_VERSION whenever you change the prompt, so the dashboard
# can tell two runs of this agent apart. TOP_K = 2 is one of the planted gaps.
PROMPT_VERSION = os.getenv("TO_FIX_AGENT_PROMPT_VERSION", "v1")
TOP_K = int(os.getenv("TO_FIX_AGENT_TOP_K", "2"))

# ---------------------------------------------------------------------------
# Knowledge base — the only source of truth the agent is allowed to cite
# ---------------------------------------------------------------------------

HANDBOOK: list[tuple[str, str]] = [
    (
        "POL-REFUND",
        "Refunds. Annual plans are refunded in full within 30 days of purchase. After day 30 "
        "an annual plan is refunded pro rata for the unused days of the term. Monthly plans are "
        "not refunded; they simply stop renewing. Add-on credit packs are never refundable.",
    ),
    (
        "POL-CANCEL",
        "Cancellation. Cancel from Account > Subscription > Cancel plan. There is no cancellation "
        "fee. Access continues until the end of the billing period already paid for, and the "
        "workspace becomes read-only for 30 days after that before deletion.",
    ),
    (
        "POL-TRIAL",
        "Free trial. Northwind Cloud offers a 14-day free trial with no credit card required. "
        "The trial includes every Pro feature but is capped at 5 seats. Trials can be extended "
        "once by 7 days on request.",
    ),
    (
        "POL-PRICING",
        "Pricing. Starter is $19 per user per month and Pro is $49 per user per month. Enterprise "
        "is custom-priced. Paying annually up front takes 20% off the list price on every plan. "
        "There is no lifetime or one-time-purchase license.",
    ),
    (
        "POL-EXPORT",
        "Data export. Export from Settings > Data > Export in CSV or JSON. Exports of up to 10 GB "
        "are generated immediately; larger exports are emailed within 24 hours. Export archives "
        "stay downloadable for 90 days.",
    ),
    (
        "POL-SLA",
        "Uptime SLA. Pro is covered by a 99.9% monthly uptime guarantee and Enterprise by 99.95%. "
        "If measured uptime falls below the guarantee, the account receives a 10% service credit; "
        "if it falls below 99.0%, the credit is 25%. Credits must be claimed within 30 days of the "
        "affected month and are applied to the next invoice. Starter has no uptime SLA.",
    ),
    (
        "POL-SEATS",
        "Seat changes. Seats can be added at any time and are billed pro rata for the remainder of "
        "the term. Seat removals take effect at the next renewal; removed seats are not refunded "
        "mid-term.",
    ),
    (
        "POL-SUPPORT",
        "Support response times. Starter gets email support with a 24-hour first-response target, "
        "Pro gets 8 hours, and Enterprise gets 1 hour plus a phone escalation line. Targets apply "
        "on business days.",
    ),
    (
        "POL-SECURITY",
        "Security and compliance. Northwind Cloud is SOC 2 Type II certified and signs a GDPR data "
        "processing addendum on request. Data residency can be pinned to the EU or US region at "
        "workspace creation and cannot be changed afterwards.",
    ),
]

ACCOUNTS: dict[str, dict[str, Any]] = {
    "ACC-4471": {
        "company": "Kestrel Analytics",
        "plan": "pro",
        "seats": 12,
        "billing": "annual",
        "days_since_purchase": 96,
        "region": "eu",
        "renews_on": "2027-01-14",
        "addon_credits_usd": 250.0,
    },
    "ACC-2210": {
        "company": "Bright Harbor Media",
        "plan": "starter",
        "seats": 3,
        "billing": "annual",
        "days_since_purchase": 12,
        "region": "us",
        "renews_on": "2027-04-02",
        "addon_credits_usd": 0.0,
    },
    "ACC-9008": {
        "company": "Vantiq Labs",
        "plan": "enterprise",
        "seats": 240,
        "billing": "annual",
        "days_since_purchase": 210,
        "region": "us",
        "renews_on": "2026-11-30",
        "addon_credits_usd": 1200.0,
    },
}

LIST_PRICE_PER_SEAT_MONTH = {"starter": 19.0, "pro": 49.0}
ANNUAL_DISCOUNT = 0.20
FULL_REFUND_WINDOW_DAYS = 30
TERM_DAYS = 365


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _handbook_store() -> InMemoryVectorStore:
    docs = [
        Document(page_content=text, metadata={"doc_id": doc_id})
        for doc_id, text in HANDBOOK
    ]
    return InMemoryVectorStore.from_documents(
        docs, OpenAIEmbeddings(model=EMBEDDING_MODEL)
    )


@tool
def search_handbook(query: str) -> str:
    """Search the Northwind Cloud policy handbook and return the most relevant entries.

    Each entry comes back prefixed with its doc id (for example [POL-REFUND]). Use this
    before answering anything about plans, pricing, billing, cancellation, refunds,
    trials, exports, SLAs, support tiers or compliance.
    """
    hits = _handbook_store().similarity_search(query, k=TOP_K)
    return "\n\n".join(f"[{d.metadata['doc_id']}] {d.page_content}" for d in hits)


@tool
def get_account(account_id: str) -> str:
    """Look up one customer account record by its id (for example ACC-4471).

    Returns JSON with the company name, plan, seat count, billing cadence, days since
    the current term was purchased, data region, renewal date and unused add-on credits.
    """
    record = ACCOUNTS.get(account_id.strip().upper())
    if record is None:
        return json.dumps(
            {"error": "account_not_found", "account_id": account_id},
        )
    return json.dumps({"account_id": account_id.strip().upper(), **record})


@tool
def estimate_refund(plan: str, seats: int, days_since_purchase: int) -> str:
    """Estimate the refund owed on an annual Northwind Cloud plan.

    Args:
        plan: "starter", "pro" or "enterprise".
        seats: number of paid seats on the account.
        days_since_purchase: days elapsed since the current annual term was paid for.

    Returns JSON with the annual contract value, the refund amount in USD, and the
    policy basis used.
    """
    plan_key = plan.strip().lower()

    if plan_key == "enterprise":
        return json.dumps(
            {
                "status": "manual_review",
                "reason": "Enterprise terms are individually contracted; billing must price the refund.",
            }
        )

    rate = LIST_PRICE_PER_SEAT_MONTH.get(plan_key)
    if rate is None:
        return json.dumps({"error": "unknown_plan", "plan": plan})

    annual_total = round(rate * 12 * (1 - ANNUAL_DISCOUNT) * seats, 2)

    if days_since_purchase <= FULL_REFUND_WINDOW_DAYS:
        refund = annual_total
        basis = f"Within the {FULL_REFUND_WINDOW_DAYS}-day full refund window."
        unused_days = TERM_DAYS - days_since_purchase
    else:
        unused_days = max(TERM_DAYS - days_since_purchase, 0)
        refund = round(annual_total * unused_days / TERM_DAYS, 2)
        basis = f"Pro rata for {unused_days} unused days of a {TERM_DAYS}-day term."

    return json.dumps(
        {
            "plan": plan_key,
            "seats": seats,
            "annual_contract_value_usd": annual_total,
            "unused_days": unused_days,
            "refund_usd": refund,
            "basis": basis,
            "note": "Add-on credit packs are excluded — they are never refundable.",
        }
    )


TOOLS = [search_handbook, get_account, estimate_refund]

# v1 prompt — intentionally the kind of prompt a team ships first: helpful in tone,
# vague about grounding, citations, tool discipline and length. The evaluation's
# acceptance criteria are strict, so the gap between the two is what shows up as
# findings in the report.
SYSTEM_PROMPT = """You are the Northwind Cloud support agent. Help customers and the \
support team with questions about Northwind Cloud plans, billing and policies.

You have a handbook search tool, an account lookup and a refund estimator available. Use \
them when they seem useful. Be friendly and thorough, and try to anticipate what the \
customer will want to know next."""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def build_agent():
    """Build (once) the ReAct agent graph."""
    llm = ChatAnthropic(model=MODEL, max_tokens=4096)
    return create_agent(model=llm, tools=TOOLS, system_prompt=SYSTEM_PROMPT)


@dataclass
class AgentAnswer:
    """One agent turn, flattened into everything the evaluation cares about."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0

    @property
    def tools_used(self) -> list[str]:
        return [call["name"] for call in self.tool_calls]


def _block_text(content: Any) -> str:
    """Message content is a plain string or a list of content blocks — flatten both."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()
    return str(content)


def answer(question: str, callbacks: list | None = None) -> AgentAnswer:
    """Run the agent on one question and return the reply plus its observable trace.

    `callbacks` is passed straight through to LangChain. The evaluation harness uses it to
    attach AgentX's AgentXCallbackHandler, which turns each graph node, LLM call and tool
    call into a real child span - the difference between an Execution Timeline showing the
    actual trajectory and a flat list folded onto one row. Purely observability: the agent
    behaves identically with or without it.
    """
    config = {"callbacks": callbacks} if callbacks else None
    state = build_agent().invoke(
        {"messages": [{"role": "user", "content": question}]}, config=config
    )
    messages = state["messages"]

    result = AgentAnswer(text="")
    pending: dict[str, dict[str, Any]] = {}

    for message in messages:
        kind = getattr(message, "type", None)

        if kind == "ai":
            result.llm_calls += 1
            usage = getattr(message, "usage_metadata", None) or {}
            result.input_tokens += usage.get("input_tokens") or 0
            result.output_tokens += usage.get("output_tokens") or 0
            for call in getattr(message, "tool_calls", None) or []:
                record = {"name": call["name"], "args": call.get("args", {}), "result": None}
                result.tool_calls.append(record)
                pending[call["id"]] = record

        elif kind == "tool":
            record = pending.get(getattr(message, "tool_call_id", ""))
            if record is not None:
                record["result"] = _block_text(message.content)

    final = messages[-1] if messages else None
    result.text = _block_text(getattr(final, "content", "")) if final else ""
    return result


def main() -> None:
    question = " ".join(sys.argv[1:]) or "How do I cancel, and do I keep access until month end?"
    print(f"Q: {question}\n")

    reply = answer(question)

    for call in reply.tool_calls:
        preview = (call["result"] or "").replace("\n", " ")[:110]
        print(f"  → {call['name']}({call['args']}) … {preview}")
    if reply.tool_calls:
        print()

    print(reply.text)
    print(
        f"\n[{MODEL} · {reply.llm_calls} llm calls · "
        f"{reply.input_tokens} in / {reply.output_tokens} out tokens]"
    )


if __name__ == "__main__":
    main()
