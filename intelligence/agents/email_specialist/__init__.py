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

# bill_specialist registers its own tools; we only need its agent
# definition to be in the registry so the delegation tool can find it.
import intelligence.agents.bill_specialist  # noqa: F401

from intelligence.composition.delegation import make_delegation_tool
from intelligence.tools.registry import register as _register_tool

# One delegation primitive for v1. Phase 2 only routes to
# bill_specialist; expense / bill_credit can be added later by:
#   1. Importing the specialist package above.
#   2. Adding another make_delegation_tool() call here.
#   3. Adding the tool name to email_specialist.tools in definition.py.
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

# Register the agent itself.
from intelligence.agents.email_specialist.definition import email_specialist  # noqa: F401
