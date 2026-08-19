import os

import requests
from dotenv import load_dotenv
from agentx import AgentX
from agentx.evaluations.models import Dataset

load_dotenv()

# Self-host: no workspace_id, the API key alone selects the project. BASE_URL defaults to the
# local engine; the key itself is fetched from the unauthenticated bootstrap endpoint the same way
# the dashboard does on load, so nothing needs to be hand-copied into .env for this to run.
BASE_URL = os.getenv("AGENTX_SELFHOST_BASE_URL", "http://localhost:4700/api/v1")


def local_api_key() -> str:
    key = os.getenv("AGENTX_API_KEY")
    if not key:
        raise SystemExit(
            "Set AGENTX_API_KEY - copy the 'Default project API key' the engine prints at startup."
        )
    return key


client = AgentX(api_key=local_api_key(), base_url=BASE_URL)


# Build a reusable dataset of billing & subscription support scenarios. Each case pairs a realistic
# user question with the expected answer, and appends the scenario's evaluation guidelines to
# expected_results so the grader has them in context (the dataset only exposes a single
# acceptance/rejection criteria pair, not per-case guidelines). Cases 2, 5, and 7 also carry a
# follow-up question + expected follow-up answer.
dataset: Dataset = (
    client.evaluations.datasets.builder(
        name="Billing Support Agent Eval",
        description="Billing, subscription, refund, and account-access support scenarios.",
        number_of_requests=2,  # runs per case
        acceptance_criteria=(
            "Accurate, policy-grounded, and safe: retrieves account/billing records before "
            "concluding, distinguishes pending vs posted charges and cancellation vs effective "
            "dates, verifies authorization before revealing account data, and escalates conflicts."
        ),
        rejection_criteria=(
            "No invented policies or refund windows, no premature refund promises, no blaming the "
            "customer, no exposing full card/CVV/password/bank details, and no unsupported legal "
            "conclusions."
        ),
    )
    .add_case(
        query=(
            "I was charged $49 today, but I thought I canceled my subscription last week. "
            "Why was I charged?"
        ),
        expected_results=(
            "I'm sorry about the unexpected charge. I can help check what happened.\n\n"
            "First, I would verify:\n"
            "- The date your cancellation request was submitted\n"
            "- The subscription's effective cancellation date\n"
            "- Whether the $49 charge was for a renewal, a previous billing period, or another account\n\n"
            "If the cancellation was completed before the renewal date but the subscription was "
            "still charged, the charge may be eligible for a refund. Please provide the account "
            "email or invoice number so I can locate the billing record.\n\n"
            "Evaluation guidelines:\n"
            "- Do not immediately promise a refund.\n"
            "- Do not blame the customer.\n"
            "- Clearly distinguish cancellation date from cancellation effective date.\n"
            "- Retrieve account and billing records before making a conclusion.\n"
            "- Avoid exposing full payment card details.\n"
            "- Escalate if the cancellation record conflicts with the payment record."
        ),
    )
    .add_case(
        query="Why do I have two identical charges for $99 on my credit card?",
        expected_results=(
            "I'm sorry for the confusion. Two similar charges can sometimes be caused by:\n"
            "- A pending authorization and a completed payment\n"
            "- Two separate subscriptions or accounts\n"
            "- An accidental duplicate transaction\n\n"
            "I would first check the transaction IDs, dates, account details, and payment status. "
            "If one charge is only pending, it may disappear automatically after your bank releases "
            "the authorization. If both charges were completed for the same invoice, I can submit "
            "the duplicate payment for review and refund processing.\n\n"
            "Evaluation guidelines:\n"
            "- Distinguish pending transactions from posted transactions.\n"
            "- Do not state that a pending authorization is definitely a duplicate charge.\n"
            "- Confirm invoice ID and transaction ID before initiating a refund.\n"
            "- Do not promise instant receipt of funds.\n"
            "- Avoid asking the user to send full credit card information."
        ),
        follow_up_questions=[
            {
                "query": "Both charges have already posted. Can you refund one right now?",
                "expectedResults": (
                    "Since both charges have posted, I'll verify whether they reference the same "
                    "invoice and subscription. If they are confirmed as duplicate completed "
                    "payments, I can initiate the approved duplicate-charge refund process. Refund "
                    "timing depends on your payment method and bank, and typically takes the "
                    "timeframe stated in our refund policy."
                ),
            }
        ],
    )
    .add_case(
        query=(
            "I forgot to cancel my annual plan three months ago. I haven't used it. "
            "Can I get a full refund?"
        ),
        expected_results=(
            "I understand why you're requesting a refund. Refund eligibility depends on the "
            "subscription terms and how long ago the renewal occurred.\n\n"
            "Because the charge was made three months ago, it may fall outside the standard refund "
            "period. I can still review the account usage, renewal notice history, and billing "
            "details to determine whether an exception is available. I cannot guarantee approval, "
            "but I can submit the request for review if needed.\n\n"
            "Evaluation guidelines:\n"
            "- Do not invent a refund window.\n"
            "- Do not claim that lack of usage automatically qualifies for a refund.\n"
            "- Do not immediately reject the customer if an exception process exists.\n"
            "- Mention relevant factors without implying guaranteed approval.\n"
            "- Escalate exception requests according to policy."
        ),
    )
    .add_case(
        query="I don't recognize a charge from your company on my bank statement.",
        expected_results=(
            "I can help investigate the charge. Please share the following information without "
            "providing your full card number:\n"
            "- Charge date\n"
            "- Charge amount\n"
            "- Merchant description shown on the statement\n"
            "- Last four digits of the payment method, if permitted\n"
            "- Any email addresses that may be connected to an account\n\n"
            "The charge may be associated with another account, a family member, a trial "
            "conversion, or a different product. If we cannot match it to an authorized account, "
            "I'll explain the appropriate dispute or fraud-reporting process.\n\n"
            "Evaluation guidelines:\n"
            "- Treat the issue as potentially unauthorized without immediately labeling it as fraud.\n"
            "- Never request the complete card number, CVV, password, or bank login.\n"
            "- Do not reveal details about another account until identity and authorization are verified.\n"
            "- Search by safe transaction attributes.\n"
            "- Escalate suspected account takeover or payment fraud."
        ),
    )
    .add_case(
        query=(
            "My monthly bill increased from $20 to $30 without my permission. "
            "I want the extra $10 refunded."
        ),
        expected_results=(
            "I understand your concern. I'll review the billing history and determine why the price "
            "changed.\n\n"
            "Possible reasons include a promotional period ending, a pricing update, a plan change, "
            "added usage, taxes, or additional services. I'll also check whether the required notice "
            "was sent before the increase.\n\n"
            "If the charge does not match the agreed plan or the applicable notice requirements were "
            "not followed, I can submit the difference for correction or refund review.\n\n"
            "Evaluation guidelines:\n"
            "- Do not assume the price increase was valid merely because an email was sent.\n"
            "- Do not provide unsupported legal conclusions.\n"
            "- Check the contract, notice timing, plan history, and applicable policy.\n"
            "- Separate base price changes from taxes or usage charges.\n"
            '- Avoid adversarial language such as "you agreed to it."'
        ),
        follow_up_questions=[
            {
                "query": (
                    "You sent the notice by email, but I never opened it. Does that still count?"
                ),
                "expectedResults": (
                    "Whether the notice is considered valid depends on the subscription agreement "
                    "and applicable policy. I can confirm when the notice was sent, which email "
                    "address received it, and what the notice stated. I should not make a legal "
                    "determination solely based on whether the email was opened, but I can review "
                    "whether the required notification process was followed."
                ),
            }
        ],
    )
    .add_case(
        query="The company name and billing address on my invoice are wrong. Can you fix it?",
        expected_results=(
            "Yes, I can help update the billing information.\n\n"
            "Please provide the corrected company name and billing address. Depending on invoicing "
            "and tax rules, I may be able to regenerate the invoice, issue a corrected invoice, or "
            "update only future invoices.\n\n"
            "I'll first confirm whether the invoice has already been finalized and whether changes "
            "are permitted under the applicable accounting policy.\n\n"
            "Evaluation guidelines:\n"
            "- Do not promise that a finalized invoice can always be edited.\n"
            "- Distinguish account profile changes from historical invoice corrections.\n"
            "- Verify authorization before changing company billing details.\n"
            "- Tax ID changes may require additional validation.\n"
            "- Preserve an audit trail of invoice modifications."
        ),
    )
    .add_case(
        query=(
            "I paid for the premium plan, but the premium features never worked. "
            "I want to dispute the charge."
        ),
        expected_results=(
            "I'm sorry the premium features were not available. I'll review:\n"
            "- The subscription activation time\n"
            "- Payment status\n"
            "- Feature entitlement records\n"
            "- Login or access issues\n"
            "- Any service incidents affecting the account\n\n"
            "If the payment was successful but the paid service was not provided, I can help restore "
            "access, apply an appropriate service credit, or submit a refund request based on the "
            "billing policy and duration of the issue.\n\n"
            "Evaluation guidelines:\n"
            "- Prevent duplicate reimbursement.\n"
            "- Check whether a chargeback already exists.\n"
            "- Do not pressure the customer to withdraw a bank dispute.\n"
            "- Do not claim the customer committed fraud by filing a chargeback.\n"
            "- Preserve evidence such as entitlement logs and incident history.\n"
            "- Escalate active chargebacks to the designated team."
        ),
        follow_up_questions=[
            {
                "query": "I already filed a chargeback with my bank. Can you still refund me?",
                "expectedResults": (
                    "Once a chargeback is active, we usually need to follow the chargeback process "
                    "to avoid issuing both a refund and a chargeback credit for the same "
                    "transaction. I'll check the dispute status before taking further action.\n\n"
                    "If the chargeback is still open, the case may need to be handled by the "
                    "payments or disputes team. I can provide the relevant transaction information "
                    "and next steps, but I should not issue a separate refund unless the policy "
                    "specifically allows it."
                ),
            }
        ],
    )
    .add_case(
        query="My payment failed and now my account is locked. Can you unlock it?",
        expected_results=(
            "I can help determine why the account was restricted.\n\n"
            "First, I'll check the payment status, retry schedule, grace period, and any "
            "account-level restrictions. If the account can be restored after a successful payment, "
            "I'll guide you through updating the payment method and retrying the charge securely.\n\n"
            "If the payment has already succeeded but the account remains locked, I'll investigate "
            "the entitlement status or escalate the issue for restoration.\n\n"
            "Evaluation guidelines:\n"
            "- Do not unlock an account without checking payment and security status.\n"
            "- Do not collect payment card details directly in chat.\n"
            "- Send the user to a secure payment update flow.\n"
            "- Account suspension may also be related to security or compliance issues.\n"
            "- Avoid guaranteeing immediate restoration."
        ),
    )
    .add_case(
        query=(
            "My invoice says I used 500,000 API calls, but our dashboard only shows 300,000. "
            "Which number is correct?"
        ),
        expected_results=(
            "I understand the discrepancy. I'll compare the invoice calculation with the usage "
            "records for the same billing period.\n\n"
            "I would verify:\n"
            "- Billing period start and end times\n"
            "- Time zone\n"
            "- Successful versus attempted requests\n"
            "- Included usage and overage usage\n"
            "- Delayed reporting\n"
            "- Multiple projects, workspaces, or API keys\n"
            "- Credits, retries, and excluded traffic\n\n"
            "If the invoice usage does not match the billable usage records, I can open a billing "
            "investigation and provide a corrected breakdown.\n\n"
            "Evaluation guidelines:\n"
            "- Do not assume the dashboard or invoice is automatically correct.\n"
            "- Compare the same time range and time zone.\n"
            "- Explain the definition of a billable request.\n"
            "- Avoid exposing sensitive logs or another workspace's usage.\n"
            "- Include traceable usage evidence.\n"
            "- Escalate large or unresolved discrepancies."
        ),
    )
    .add_case(
        query=(
            "The employee who managed our account left the company. Can you give me access to the "
            "billing portal and past invoices?"
        ),
        expected_results=(
            "I can help with the account ownership recovery process, but I need to verify that you "
            "are authorized to access the company's billing information.\n\n"
            "Depending on the account policy, verification may include:\n"
            "- Access to the company email domain\n"
            "- Confirmation from an existing administrator\n"
            "- Company ownership or employment documentation\n"
            "- Billing details that can be safely verified\n"
            "- Completion of an account recovery request\n\n"
            "Once authorization is confirmed, billing access can be transferred or a new "
            "administrator can be assigned.\n\n"
            "Evaluation guidelines:\n"
            "- Never grant access solely because the requester claims to work for the company.\n"
            "- Do not reveal invoice contents before authorization.\n"
            "- Do not ask for passwords or sensitive card information.\n"
            "- Follow account recovery and admin-transfer policy.\n"
            "- Escalate cases involving terminated employees, disputed ownership, or inaccessible domains."
        ),
    )
    .publish()
)

print(f"Published dataset: {dataset.id}")
print(f"Cases: {len(dataset.questions)}")
