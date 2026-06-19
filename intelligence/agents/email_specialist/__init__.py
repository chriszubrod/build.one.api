"""Email specialist agent package — pure orchestrator for the polled
invoice inbox.

The email_specialist is system-triggered (not Scout-initiated) by the
scheduler-driven /admin/email/process_one endpoint. For each pending
EmailMessage it:

  1. Reads the email + attachment metadata.
  2. Classifies the email (invoice / non-invoice / packet) using DI on
     attachments that look invoice-shaped.
  3. Bridges the chosen attachment(s) into Attachment rows so they
     satisfy bill_specialist.create_bill's contract.
  4. Delegates to bill_specialist with a packaged task description
     (DI vendor name + sender domain + extracted fields + bridged
     attachment_public_id + source_email_message_public_id).
  5. Stamps a final outcome on the EmailMessage row + Outlook category.

It carries grants ONLY on Email Messages (read+update). All entity
side effects flow through delegation; bill_specialist's create_bill
approval card is the protective layer.

Importing this package triggers tool + delegation + agent
registration.
"""
# Email-message-side tools (read, extract, bridge, mark outcome)
import entities.email_message.intelligence.tools  # noqa: F401

# Specialists register their own tools; we only need their agent
# definitions to be in the registry so the delegation tools can find
# them.
import intelligence.agents.bill_specialist  # noqa: F401
import intelligence.agents.contract_labor_specialist  # noqa: F401
import intelligence.agents.expense_specialist  # noqa: F401

from intelligence.composition.delegation import make_delegation_tool
from intelligence.tools.registry import register as _register_tool

# Delegation primitives. Each one points at a downstream specialist
# that knows how to materialize a specific entity from the email's
# extracted signals. Adding another specialist: import its package
# above + register another delegation tool here + add the tool name
# to email_specialist.tools in definition.py.

_register_tool(make_delegation_tool(
    name="delegate_to_bill_specialist",
    target_agent="bill_specialist",
    description=(
        "Hand a draft-bill creation task off to the Bill specialist "
        "agent. Use this once you've extracted DI fields, validated "
        "them (confidence >= 0.7), and bridged the email attachment "
        "to a regular Attachment row.\n\n"
        "Pass a self-contained task description in markdown that "
        "carries:\n"
        "  • DI-extracted vendor name (so bill_specialist can "
        "    search_vendors)\n"
        "  • Sender email domain (e.g. `laura@walkerlumber.com` → "
        "    `walkerlumber.com`) as a supplementary disambiguation "
        "    hint when the DI vendor name is ambiguous\n"
        "  • Bill date (ISO YYYY-MM-DD)\n"
        "  • Due date (if extracted)\n"
        "  • Bill number\n"
        "  • Total amount\n"
        "  • The bridged Attachment public_id  ← REQUIRED for "
        "    create_bill\n"
        "  • The source EmailMessage public_id ← so the bill row "
        "    links back to its source\n\n"
        "The specialist's create_bill is approval-gated — the user "
        "sees the proposed values and can edit before the draft "
        "commits. Your delegation returns the specialist's final "
        "answer (markdown), which you can quote in your own final "
        "message back to the runner."
    ),
))

_register_tool(make_delegation_tool(
    name="delegate_to_contract_labor_specialist",
    target_agent="contract_labor_specialist",
    description=(
        "Hand a forwarded worker timesheet email off to the Contract "
        "Labor specialist agent. Use this when you've detected the "
        "email is a worker-submitted timesheet (no invoice "
        "attachments + body has clock-in/clock-out + a job-site "
        "address) — see Step 1c.\n\n"
        "Pass a self-contained task description in markdown that "
        "carries:\n"
        "  • Sender email address (the worker's from_address — used "
        "    to bind back to a contract-labor Vendor)\n"
        "  • Subject line (carries the work date in `M/D` form)\n"
        "  • Received year (so the specialist can resolve a bare "
        "    `M/D` to a full date)\n"
        "  • Body content (the worker's timesheet text: address, "
        "    times, work description, signature)\n\n"
        "The specialist binds the sender to a Vendor, resolves the "
        "address to a Project (via project_specialist), parses the "
        "times, and creates a draft `pending_review` ContractLabor "
        "row. No approval gate — the row is a draft awaiting human "
        "review for rate / markup / SubCostCode. The specialist "
        "returns its final markdown answer; quote the gist in your "
        "own final message."
    ),
))

_register_tool(make_delegation_tool(
    name="delegate_to_expense_specialist",
    target_agent="expense_specialist",
    description=(
        "Hand a draft-Expense creation task off to the Expense specialist "
        "agent. Use this when an attachment is a point-of-sale / retail "
        "receipt (a card purchase — Home Depot, gas, hardware, supplies — "
        "NOT an AP invoice with payment terms and NOT a vendor credit memo). "
        "Use it once you've extracted the receipt fields (confidence >= 0.95) "
        "and bridged the email attachment to a regular Attachment row.\n\n"
        "Pass a self-contained task description in markdown that carries:\n"
        "  • DI-extracted vendor / merchant name (for find_vendor_for_invoice)\n"
        "  • Sender email domain as a disambiguation tiebreaker\n"
        "  • Expense date (ISO YYYY-MM-DD)\n"
        "  • Reference / receipt / transaction number (-> reference_number)\n"
        "  • Total amount\n"
        "  • is_credit hint: true if the receipt is a return / refund / card "
        "    credit, else false (default false when unsure)\n"
        "  • The bridged Attachment public_id  ← REQUIRED for create_expense\n"
        "  • The source EmailMessage public_id ← traceability\n"
        "  • Job-site / Ship To address if the receipt carries one\n\n"
        "create_expense is NOT approval-gated — the specialist creates the "
        "draft Expense (with the receipt attached + an inline summary line) "
        "immediately; it awaits a human to review + complete. The delegation "
        "returns the specialist's final markdown answer; quote the gist in "
        "your own final message."
    ),
))

# Register the agent itself.
from intelligence.agents.email_specialist.definition import email_specialist  # noqa: F401
