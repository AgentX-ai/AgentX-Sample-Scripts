"""
Billing Dispute & Refund Investigation — multi-LLM LangChain demo.

This is the "kitchen sink" tracing demo: one customer request drives a whole
multi-step, multi-model agent workflow, and *everything* is traced in detail
into a single AgentX session (grouped by ``SESSION_ID``).

Every step is captured through the real LangChain integration (the same
mechanism shown in ``langchain_agent_test_with_rag.py``):

  * Retrieval    → a real ``InMemoryVectorStore`` retriever invoked with the
                   callback handler (``retriever.invoke(..., callbacks=[h])``),
                   so the query, retrieved chunks and doc counts show up.
  * Tool use     → real ``@tool`` functions bound to ``create_agent`` agents and
                   invoked with the handler, so each tool call (args, output,
                   latency, errors, retries) is traced automatically.
  * LLM steps    → each specialist is its own ``prompt | llm`` chain invoked
                   with the handler, so model, tokens and latency are captured
                   per role — one detailed trace each.

Workflow:

    1. Intent & risk classification        (LLM, fast model)
    2. Retrieval query rewriting            (LLM, fast model)
    3. Account & billing investigation      (create_agent + 6 read tools)
    4. Policy-eligibility specialist        (LLM, strong model) ┐ run in
       + date-aware policy retrieval (RAG)                      │ parallel
    5. Fraud / anomaly specialist           (LLM, fast model)   ┘
    6. Action validation                    (LLM, guardrail)
    7. Action execution                     (create_agent + action tools:
                                             refund w/ timeout + idempotent
                                             retry, cancel, disable renew, ...)
    8. Customer-facing response             (LLM, fast model)

The most interesting trace moment is the *outdated policy correction*: the
first retrieval returns the current 7-day refund policy, but the charge
happened while the older 14-day policy was in effect, so the agent re-retrieves
the policy that was active on the renewal date and changes its mind. Both
retrievals show up on the policy-specialist trace.

Run:  python sdk_test/langchain_test/langchain_billing_dispute_investigation.py

--------------------------------------------------------------------------------
Eval scenarios — flip a single hidden fact in the CONFIG block below to
reproduce the table from the demo script:

  * Full refund .................. default (cancel attempt before renewal, no usage)
  * Deny / grace period .......... set CANCEL_ATTEMPT_DATE after RENEWAL_DATE
  * Partial credit / deny ........ set USAGE_SESSIONS_AFTER_RENEWAL high
  * Policy-date correction ....... keep RENEWAL_DATE before POLICY_CHANGE_DATE
  * Refund timeout / retry ....... keep REFUND_FAILS_FIRST_ATTEMPT = True
  * Escalate (over authority) .... set CHARGE_AMOUNT above REFUND_AUTHORITY_LIMIT
  * Escalate (fraud) ............. set PRIOR_REFUND_COUNT high / add chargebacks
--------------------------------------------------------------------------------
"""

import os
import time
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agentx import AgentX
from agentx.integrations.langchain import AgentXCallbackHandler
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_core.tools import tool
from langchain.agents import create_agent

load_dotenv()

client = AgentX(
    api_key=os.getenv("AGENTX_API_KEY"),
    base_url=os.getenv("BASE_URL"),
    workspace_id=os.getenv("WORKSPACE_ID"),
)

handler = AgentXCallbackHandler(
    tracer=client.tracer,
    name="support-agent-billing",  # custom name for the agent
    session_id="session-001",  # custom session id for the agent
)
# name/session_id above are only used for calls made outside an active
# `with tracer.trace(...)` span. investigate() below wraps every call in
# one orchestrator span, so its own name/session_id govern instead — every
# chain/agent/retriever invocation merges into that single trace rather than
# sending its own.

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# A cheap/fast model for classification, rewriting and drafting; a stronger
# model for the grounded policy reasoning. Using two model tiers is what makes
# the per-trace cost/latency comparison in the AgentX session interesting.
FAST_MODEL = "gpt-4o-mini"
STRONG_MODEL = "gpt-4o"

# One session id ties every trace below together in the AgentX UI.
SESSION_ID = f"billing-dispute-{int(time.time())}"

# The exact demo prompt: it can't be answered from knowledge alone, and it
# can't be answered from account tools alone — it needs both, plus reasoning.
USER_MESSAGE = "I was charged $499 for an annual renewal yesterday. I tried to cancel the day before, but the page wasn't working. Please refund the charge, and ensure I won't be billed again."

# ---------------------------------------------------------------------------
# CONFIG — hidden facts. Flip these to reproduce the eval scenarios.
# ---------------------------------------------------------------------------
CUSTOMER_ID = "cust_10042"
SUBSCRIPTION_ID = "sub_annual_5567"
INVOICE_ID = "inv_2025_11_15_0001"
CHARGE_AMOUNT = 499.00

# The company tightened its refund window from 14 days to 7 days on this date.
POLICY_CHANGE_DATE = date(2026, 1, 1)
# The renewal happened while the OLD (14-day) policy was still in effect, so the
# agent must not apply the current 7-day policy — this drives the correction.
RENEWAL_DATE = date(2025, 11, 15)
CANCEL_ATTEMPT_DATE = date(2025, 11, 14)  # one day before renewal
USAGE_SESSIONS_AFTER_RENEWAL = 0  # no product usage after renewal
PRIOR_REFUND_COUNT = 0  # clean refund history
CHARGEBACKS = 0
REFUND_AUTHORITY_LIMIT = 1000.00  # agent can auto-refund up to this
REFUND_FAILS_FIRST_ATTEMPT = True  # simulate a gateway timeout + retry

SUPPORT_MESSAGE = "I'm trying to cancel but the button is not working."


# ===========================================================================
# Structured outputs — makes the multi-LLM architecture explicit in the trace.
# ===========================================================================
class IntentClassification(BaseModel):
    intents: List[str] = Field(
        description="e.g. billing_dispute, refund_request, cancellation_verification, future_billing_prevention"
    )
    financially_sensitive: bool
    risk_score: float = Field(description="0.0 (routine) to 1.0 (high risk)")
    confidence: float
    disputed_amount: Optional[float] = None
    subscription_type: Optional[str] = None
    claims_cancellation_attempt: bool = False


class RetrievalPlan(BaseModel):
    queries: List[str] = Field(description="Precise knowledge-base search queries")


class EligibilityDecision(BaseModel):
    refund_eligible: bool
    reason: str
    policy_clause: str
    maximum_refund: float
    confidence: float
    requires_human_approval: bool


class RiskAssessment(BaseModel):
    risk_level: str = Field(description="low | medium | high")
    signals: List[str]
    recommended_action: str = Field(description="proceed | review | block")


class ActionValidation(BaseModel):
    approved: bool
    checks: List[str]
    issues: List[str]


# ===========================================================================
# Versioned policy knowledge base -> a real, date-filterable vector store.
# ===========================================================================
_FAR_FUTURE = date(9999, 12, 31).toordinal()

POLICY_DOCS: List[Dict[str, Any]] = [
    {
        "id": "refund-policy-v3",
        "version": "v3-current",
        "effective_from": date(2026, 1, 1),
        "effective_to": None,
        "topic": "annual subscription refund window",
        "text": "Refunds for annual subscriptions are permitted within 7 days of the renewal charge.",
    },
    {
        "id": "refund-policy-v2",
        "version": "v2",
        "effective_from": date(2025, 6, 1),
        "effective_to": date(2025, 12, 31),
        "topic": "annual subscription refund window",
        "text": "Refunds for annual subscriptions are permitted within 14 days of the renewal charge.",
    },
    {
        "id": "failed-cancellation-exception",
        "version": "v1",
        "effective_from": date(2025, 1, 1),
        "effective_to": None,
        "topic": "failed cancellation exception",
        "text": (
            "If a customer made a documented attempt to cancel before renewal but the "
            "cancellation did not complete due to a product error, a full refund is "
            "permitted regardless of the standard refund window."
        ),
    },
    {
        "id": "enterprise-nonrefundable",
        "version": "v1",
        "effective_from": date(2024, 1, 1),
        "effective_to": None,
        "topic": "enterprise annual non-refundable",
        "text": "Enterprise annual subscriptions are non-refundable except where required by law.",
    },
    {
        "id": "grace-period",
        "version": "v1",
        "effective_from": date(2024, 1, 1),
        "effective_to": None,
        "topic": "cancellation grace period",
        "text": "A 3-day grace period applies to cancellations submitted immediately before a renewal.",
    },
    {
        "id": "escalation-policy",
        "version": "v1",
        "effective_from": date(2024, 1, 1),
        "effective_to": None,
        "topic": "refund approval limits and escalation",
        "text": (
            "Refunds above the agent authorization limit of $1000, or cases with elevated "
            "fraud risk, must be escalated to a human specialist."
        ),
    },
]

_policy_documents = [
    Document(
        page_content=f"{d['topic']}: {d['text']}",
        metadata={
            "id": d["id"],
            "version": d["version"],
            "topic": d["topic"],
            "effective_from": d["effective_from"].toordinal(),
            "effective_to": (
                d["effective_to"].toordinal() if d["effective_to"] else _FAR_FUTURE
            ),
        },
    )
    for d in POLICY_DOCS
]

_embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY)
_policy_store = InMemoryVectorStore.from_documents(
    _policy_documents, embedding=_embeddings
)


def policy_retriever_as_of(as_of: date, k: int = 3):
    """Retriever that only returns policies in effect on ``as_of`` — this is
    what lets the agent select the policy version active on the transaction
    date instead of the current one."""
    as_of_ord = as_of.toordinal()
    return _policy_store.as_retriever(
        search_kwargs={
            "k": k,
            "filter": lambda doc: doc.metadata["effective_from"]
            <= as_of_ord
            <= doc.metadata["effective_to"],
        }
    )


def fmt_docs(docs: List[Document]) -> str:
    return "\n".join(f"- [{d.metadata.get('version')}] {d.page_content}" for d in docs)


# ===========================================================================
# Account / billing tools (read-only) — bound to the investigation agent.
# ===========================================================================
@tool
def get_customer_profile(customer_id: str) -> dict:
    """Return the customer's profile (name, email, plan, account type, tenure)."""
    time.sleep(0.05)
    return {
        "customer_id": customer_id,
        "name": "Jordan Rivera",
        "email": "jordan.rivera@example.com",
        "plan": "Pro Annual",
        "account_type": "individual",
        "tenure_months": 26,
    }


@tool
def get_subscription_history(customer_id: str) -> dict:
    """Return the subscription, its renewal date, auto-renew flag and any cancellation attempts."""
    time.sleep(0.06)
    return {
        "subscription_id": SUBSCRIPTION_ID,
        "plan": "Pro Annual",
        "status": "active",
        "renewal_date": RENEWAL_DATE.isoformat(),
        "auto_renew": True,
        "cancellation_attempts": [
            {
                "date": CANCEL_ATTEMPT_DATE.isoformat(),
                "step_reached": "confirm_final_step",
                "completed": False,
                "note": "User reached the final confirmation step but it never submitted.",
            }
        ],
    }


@tool
def get_invoice(invoice_id: str) -> dict:
    """Return invoice details (amount, currency, issue date, whether refundable)."""
    time.sleep(0.05)
    return {
        "invoice_id": invoice_id,
        "subscription_id": SUBSCRIPTION_ID,
        "amount": CHARGE_AMOUNT,
        "currency": "USD",
        "issued_date": RENEWAL_DATE.isoformat(),
        "description": "Pro Annual renewal",
        "refundable": True,
    }


@tool
def get_payment_status(invoice_id: str) -> dict:
    """Return payment/capture status and any amount already refunded for an invoice."""
    time.sleep(0.05)
    return {
        "invoice_id": invoice_id,
        "status": "paid",
        "captured": True,
        "refunded_amount": 0.0,
    }


@tool
def get_product_usage(customer_id: str, date_range: str) -> dict:
    """Return product usage (sessions, active minutes) for a customer over a date range."""
    time.sleep(0.05)
    return {
        "customer_id": customer_id,
        "date_range": date_range,
        "sessions": USAGE_SESSIONS_AFTER_RENEWAL,
        "active_minutes": USAGE_SESSIONS_AFTER_RENEWAL * 12,
    }


@tool
def get_support_history(customer_id: str) -> dict:
    """Return recent support chats plus prior refund count and chargeback count."""
    time.sleep(0.06)
    return {
        "customer_id": customer_id,
        "recent_chats": [
            {
                "date": CANCEL_ATTEMPT_DATE.isoformat(),
                "channel": "live_chat",
                "message": SUPPORT_MESSAGE,
                "resolved": False,
            }
        ],
        "prior_refund_count": PRIOR_REFUND_COUNT,
        "chargebacks": CHARGEBACKS,
    }


# ===========================================================================
# Action tools (state-changing) — bound to the execution agent.
# ===========================================================================
_REFUND_LEDGER: Dict[str, Dict[str, Any]] = {}
_refund_attempts = {"count": 0}


@tool
def check_refund_status(invoice_id: str) -> dict:
    """Check whether a refund has already been recorded for an invoice (idempotency guard)."""
    time.sleep(0.04)
    rec = _REFUND_LEDGER.get(f"refund-{invoice_id}")
    return {"status": "completed" if rec else "not_found", "refund": rec}


@tool
def issue_refund(invoice_id: str, amount: float, reason_code: str) -> dict:
    """
    Issue a refund for an invoice. Idempotent per invoice: retrying with the
    same invoice never double-refunds. The first call may time out — if it does,
    call check_refund_status and then retry once.
    """
    time.sleep(0.05)
    key = f"refund-{invoice_id}"
    if key in _REFUND_LEDGER:
        return {**_REFUND_LEDGER[key], "idempotent_replay": True}

    _refund_attempts["count"] += 1
    if REFUND_FAILS_FIRST_ATTEMPT and _refund_attempts["count"] == 1:
        # Return a timeout status (rather than raising) so the agent sees the
        # failure, checks status, and safely retries — no refund is recorded,
        # so the retry is not a double-refund.
        return {
            "status": "timeout",
            "invoice_id": invoice_id,
            "error": "Payment gateway timed out while issuing refund. Call check_refund_status, then retry.",
        }

    record = {
        "refund_id": f"re_{invoice_id}",
        "invoice_id": invoice_id,
        "amount": amount,
        "reason_code": reason_code,
        "status": "completed",
    }
    _REFUND_LEDGER[key] = record
    return record


@tool
def cancel_subscription(subscription_id: str, effective_immediately: bool) -> dict:
    """Cancel a subscription, optionally effective immediately."""
    time.sleep(0.05)
    return {
        "subscription_id": subscription_id,
        "status": "canceled",
        "effective_immediately": effective_immediately,
    }


@tool
def disable_auto_renew(subscription_id: str) -> dict:
    """Disable auto-renew for a subscription so it will not be billed again."""
    time.sleep(0.04)
    return {"subscription_id": subscription_id, "auto_renew": False}


@tool
def create_case_note(customer_id: str, summary: str) -> dict:
    """Record an audit case note for the customer."""
    time.sleep(0.04)
    return {
        "customer_id": customer_id,
        "case_note_id": "note_88231",
        "summary": summary,
    }


@tool
def send_confirmation_email(customer_id: str, template_id: str) -> dict:
    """Send a confirmation email to the customer using a template."""
    time.sleep(0.04)
    return {"customer_id": customer_id, "template_id": template_id, "delivered": True}


ACCOUNT_TOOLS = [
    get_customer_profile,
    get_subscription_history,
    get_invoice,
    get_payment_status,
    get_product_usage,
    get_support_history,
]
ACTION_TOOLS = [
    issue_refund,
    check_refund_status,
    cancel_subscription,
    disable_auto_renew,
    create_case_note,
    send_confirmation_email,
]


# ===========================================================================
# LLM helpers — every call is its own trace (own model, tokens, latency).
# ===========================================================================
def run_structured(name, model, system, payload, schema, handler=None):
    llm = ChatOpenAI(
        model=model, temperature=0, api_key=OPENAI_API_KEY
    ).with_structured_output(schema)
    prompt = ChatPromptTemplate.from_messages(
        [("system", system), ("human", "{payload}")]
    )
    chain = prompt | llm
    return chain.invoke({"payload": payload}, config={"callbacks": [handler]})


def run_text(name, model, system, payload, handler=None):
    llm = ChatOpenAI(model=model, temperature=0, api_key=OPENAI_API_KEY)
    prompt = ChatPromptTemplate.from_messages(
        [("system", system), ("human", "{payload}")]
    )
    chain = prompt | llm | StrOutputParser()
    return chain.invoke({"payload": payload}, config={"callbacks": [handler]})


def run_agent(name, model, tools, system_prompt, user_content):
    """Create a create_agent agent, run it with the handler, return final text."""
    agent = create_agent(
        ChatOpenAI(model=model, temperature=0, api_key=OPENAI_API_KEY),
        tools=tools,
        system_prompt=system_prompt,
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_content}]},
        config={"callbacks": [handler]},
    )
    return result["messages"][-1].content


# ===========================================================================
# Orchestration — the whole investigation runs inside one orchestrator span.
# ===========================================================================
def investigate() -> str:
    with client.tracer.trace(
        "billing-dispute-orchestrator",
        session_id=SESSION_ID,
        framework="langchain",
        metadata={"use_case": "billing_dispute_refund", "customer_id": CUSTOMER_ID},
    ) as orch:
        orch.input = USER_MESSAGE

        # ---- 1. Intent & risk classification ------------------------------
        intent = run_structured(
            "intent-risk-classifier",
            FAST_MODEL,
            "You triage customer billing messages. Identify all intents, whether the "
            "request is financially sensitive, a 0-1 risk score, your confidence, the "
            "disputed amount, the subscription type, and whether the customer claims a "
            "cancellation attempt. Financially sensitive requests must never be "
            "auto-executed without downstream checks.",
            USER_MESSAGE,
            IntentClassification,
            handler=handler,
        )
        print(
            f"[1] intents={intent.intents} financially_sensitive={intent.financially_sensitive} "
            f"risk={intent.risk_score:.2f}"
        )

        # ---- 2. Retrieval query rewriting ---------------------------------
        plan = run_structured(
            "query-rewriter",
            FAST_MODEL,
            "You generate precise internal knowledge-base search queries for a support "
            "agent handling a billing dispute. Cover refund policy, cancellation rules, "
            "grace periods, failed-cancellation exceptions, and escalation limits.",
            f"Customer message: {USER_MESSAGE}\nDetected intents: {intent.intents}",
            RetrievalPlan,
            handler=handler,
        )
        query_text = (
            " ; ".join(plan.queries)
            or "annual subscription refund and cancellation policy"
        )
        print(f"[2] retrieval queries={plan.queries}")

        # ---- 3. Account & billing investigation (agent + 6 read tools) ----
        # A real create_agent run: the agent calls the account/billing tools and
        # every tool call is captured on this trace by the callback handler.
        investigation_summary = run_agent(
            "account-investigation",
            FAST_MODEL,
            ACCOUNT_TOOLS,
            "You are a billing investigator. You MUST call every one of these tools "
            "exactly once to gather the full picture before answering: "
            "get_customer_profile, get_subscription_history, get_invoice, "
            "get_payment_status, get_product_usage, get_support_history. Then summarize "
            "the renewal date, cancellation-attempt date and whether it completed, any "
            "support evidence, product usage after renewal, and the account type.",
            f"Investigate customer {CUSTOMER_ID}, subscription {SUBSCRIPTION_ID}, invoice "
            f"{INVOICE_ID}. Product-usage date range: {RENEWAL_DATE.isoformat()}..{date.today().isoformat()}.",
        )
        print(f"[3] investigation summary: {investigation_summary[:160]}...")

        # Facts used for downstream reasoning come from the (deterministic) mock
        # backend so the demo narrative is reproducible run to run.
        evidence = {
            "renewal_date": RENEWAL_DATE.isoformat(),
            "cancellation_attempt_date": CANCEL_ATTEMPT_DATE.isoformat(),
            "cancellation_completed": False,
            "support_evidence": SUPPORT_MESSAGE,
            "usage_after_renewal": USAGE_SESSIONS_AFTER_RENEWAL,
            "account_type": "individual",
            "invoice_refundable": True,
            "prior_refund_count": PRIOR_REFUND_COUNT,
            "chargebacks": CHARGEBACKS,
            "tenure_months": 26,
        }

        # ---- 4 & 5. Policy specialist (+ RAG) and risk specialist in parallel
        #
        # Each runs on its own thread via ThreadPoolExecutor below. The
        # tracer's active-span tracking is per-thread, so `orch` (opened on
        # the main thread) isn't automatically visible inside a worker
        # thread — wrap each worker body in `client.tracer.use_span(orch)`
        # to attach it, so these steps still land on the orchestrator trace
        # instead of becoming their own separate traces.
        def run_policy():
            with client.tracer.use_span(orch):
                # Same handler for both retrievals and the specialist LLM, so
                # the retrieved chunks attach to the policy-specialist run.

                # Initial retrieval returns the CURRENT policy (7-day window)...
                current_docs = policy_retriever_as_of(date.today()).invoke(
                    query_text, config={"callbacks": [handler]}
                )
                outdated = RENEWAL_DATE < POLICY_CHANGE_DATE
                # ...but the charge predates the policy change, so re-retrieve
                # the policy that was actually in effect on the renewal date.
                effective_docs = current_docs
                if outdated:
                    effective_docs = policy_retriever_as_of(RENEWAL_DATE).invoke(
                        "annual subscription refund window failed cancellation exception escalation limit",
                        config={"callbacks": [handler]},
                    )

                payload = (
                    f"Relevant policy passages (active on the renewal date):\n{fmt_docs(effective_docs)}\n\n"
                    f"Facts:\n"
                    f"- Renewal date: {evidence['renewal_date']}\n"
                    f"- Cancellation attempt date: {evidence['cancellation_attempt_date']} "
                    f"(completed: {evidence['cancellation_completed']})\n"
                    f"- Support evidence: \"{evidence['support_evidence']}\"\n"
                    f"- Product usage after renewal: {evidence['usage_after_renewal']} sessions\n"
                    f"- Account type: {evidence['account_type']}\n"
                    f"- Charge amount: ${CHARGE_AMOUNT}\n"
                    f"- Agent refund authorization limit: ${REFUND_AUTHORITY_LIMIT}\n"
                )
                decision = run_structured(
                    "policy-specialist",
                    STRONG_MODEL,  # stronger model for grounded policy reasoning
                    "You are a refund-policy specialist. Using ONLY the provided policy "
                    "passages and facts, decide refund eligibility. Prefer the most specific "
                    "applicable clause (e.g. a failed-cancellation exception overrides the "
                    "standard window). Return the maximum refund and whether human approval "
                    "is required.",
                    payload,
                    EligibilityDecision,
                    handler=handler,
                )
                return (
                    decision,
                    [d.metadata.get("version") for d in current_docs],
                    [d.metadata.get("version") for d in effective_docs],
                    outdated,
                )

        def run_risk():
            with client.tracer.use_span(orch):
                return run_structured(
                    "risk-specialist",
                    FAST_MODEL,
                    "You are a fraud/anomaly specialist. Look for repeated refunds, multiple "
                    "accounts, heavy post-renewal usage, chargebacks, or inconsistent claims. "
                    "Return a risk level, the signals you found, and a recommended action.",
                    f"Assess fraud/abuse risk for this refund request.\n"
                    f"- Prior refund count: {evidence['prior_refund_count']}\n"
                    f"- Chargebacks: {evidence['chargebacks']}\n"
                    f"- Product usage after renewal: {evidence['usage_after_renewal']} sessions\n"
                    f"- Account tenure: {evidence['tenure_months']} months\n"
                    f"- Claim consistency: support log corroborates the customer's story.\n",
                    RiskAssessment,
                    handler=handler,
                )

        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_policy = ex.submit(run_policy)
            fut_risk = ex.submit(run_risk)
            eligibility, initial_versions, effective_versions, outdated = (
                fut_policy.result()
            )
            risk: RiskAssessment = fut_risk.result()
        print(
            f"[4] policy: initial={initial_versions} outdated={outdated} corrected={effective_versions} "
            f"-> eligible={eligibility.refund_eligible} clause='{eligibility.policy_clause}' max=${eligibility.maximum_refund}"
        )
        print(
            f"[5] risk: level={risk.risk_level} action={risk.recommended_action} signals={risk.signals}"
        )

        # ---- 6. Decision orchestration + action validation ---------------
        # Resolution policy when specialists disagree: any non-"proceed" risk
        # recommendation or elevated risk overrides an eligible refund and
        # forces escalation. Refunds over authority also escalate.
        escalate = (
            not eligibility.refund_eligible
            or eligibility.requires_human_approval
            or eligibility.maximum_refund > REFUND_AUTHORITY_LIMIT
            or risk.risk_level == "high"
            or risk.recommended_action != "proceed"
        )
        refund_amount = min(CHARGE_AMOUNT, eligibility.maximum_refund)
        proposed_actions = [
            f"issue_refund(amount={refund_amount}, reason=FAILED_CANCEL_ATTEMPT)",
            "cancel_subscription(effective_immediately=true)",
            "disable_auto_renew()",
            "create_case_note(...)",
            "send_confirmation_email(...)",
        ]

        validation = run_structured(
            "action-validator",
            FAST_MODEL,
            "You are a pre-execution safety guardrail. Verify the proposed actions are "
            "supported by the evidence and eligibility decision: the invoice is "
            "refundable, the refund amount matches the charge, the correct subscription "
            "is being canceled, and every action is justified. Approve only if all checks "
            "pass.",
            f"Proposed actions: {proposed_actions}\n"
            f"Eligibility: eligible={eligibility.refund_eligible}, max_refund=${eligibility.maximum_refund}, "
            f"clause='{eligibility.policy_clause}'\n"
            f"Invoice refundable: {evidence['invoice_refundable']}, charge=${CHARGE_AMOUNT}\n"
            f"Subscription to cancel: {SUBSCRIPTION_ID}\n"
            f"Escalation required: {escalate}",
            ActionValidation,
            handler=handler,
        )
        print(
            f"[6] validation: approved={validation.approved} issues={validation.issues}"
        )

        # ---- 7. Action execution (agent + action tools) ------------------
        if escalate or not validation.approved:
            outcome = run_agent(
                "action-escalation",
                FAST_MODEL,
                [create_case_note],
                "This refund cannot be auto-approved. Do NOT issue a refund. Call "
                "create_case_note once to log the case for a human specialist, including "
                "the reason it was escalated, then confirm.",
                f"Escalate customer {CUSTOMER_ID}: eligible={eligibility.refund_eligible}, "
                f"risk={risk.risk_level}, over_authority={eligibility.maximum_refund > REFUND_AUTHORITY_LIMIT}, "
                f"validation_approved={validation.approved}.",
            )
            decision_label = "escalated to a human specialist"
        else:
            outcome = run_agent(
                "action-execution",
                FAST_MODEL,
                ACTION_TOOLS,
                "You execute approved refund actions safely. Follow these steps in order:\n"
                "1. Call issue_refund for the invoice. If it times out or errors, call "
                "check_refund_status; if no refund exists yet, call issue_refund one more "
                "time (it is idempotent, so this is safe).\n"
                "2. Call cancel_subscription with effective_immediately=true.\n"
                "3. Call disable_auto_renew.\n"
                "4. Call create_case_note summarizing the refund and cancellation.\n"
                "5. Call send_confirmation_email with template_id 'refund_confirmation'.\n"
                "Then confirm everything you did.",
                f"Execute the approved resolution for customer {CUSTOMER_ID}: refund "
                f"invoice {INVOICE_ID} for ${refund_amount} with reason_code "
                f"FAILED_CANCEL_ATTEMPT, cancel subscription {SUBSCRIPTION_ID}, and disable "
                f"auto-renew.",
            )
            decision_label = "refund issued"
        print(f"[7] execution: {outcome[:160]}...")

        # ---- 8. Customer-facing response ----------------------------------
        response = run_text(
            "response-writer",
            FAST_MODEL,
            "You write the final customer-facing reply for a support agent. Be warm, "
            "concise, and factual. Explain what you found and what you did. Reference the "
            "relevant policy in plain language but do NOT expose internal reasoning, "
            "confidence scores, or system details.",
            f"Customer message: {USER_MESSAGE}\n"
            f"Decision: {decision_label}\n"
            f"Refund amount: ${refund_amount}\n"
            f"Policy clause: {eligibility.policy_clause}\n"
            f"Reason: {eligibility.reason}\n"
            f"Actions taken: {outcome}",
            handler=handler,
        )
        orch.output = response
        return response


if __name__ == "__main__":
    final = investigate()
    print("\n=== Customer-facing response ===")
    print(final)
    client.tracer.flush(timeout=15)
