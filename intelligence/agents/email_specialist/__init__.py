"""Email specialist agent package — pure orchestrator for the polled
invoice inbox.

The email_specialist is system-triggered (not Build.One-initiated) by the
scheduler-driven /admin/email/process_one endpoint. For each pending
EmailMessage it:

  1. Reads the email + attachment metadata.
  2. Classifies the email (invoice / receipt / timesheet / reply / noise)
     using DI on attachments that look document-shaped.
  3. Bridges the chosen attachment(s) into Attachment rows.
  4. Builds an EntityActionEnvelope and hands it to the Build.One
     orchestrator via `delegate_to_buildone_orchestrator`, which routes by
     `entity_type` to the correct specialist (bill / expense / contract_labor).
  5. Stamps a final outcome on the EmailMessage row + Outlook category.

It carries grants ONLY on Email Messages (read+update). All entity side
effects flow through Build.One; the specialists' own gates (e.g.
bill_specialist's complete_bill approval) remain the protective layer.

Importing this package triggers tool + agent registration.
"""
# Email-message-side tools (read, extract, bridge, mark outcome).
import entities.email_message.intelligence.tools  # noqa: F401

# Entity-tool modules that register email_specialist's own detection tools:
#   find_bill_by_conversation_id            (entities/bill/intelligence/tools.py)
#   find_contract_labor_by_conversation_id  (entities/contract_labor/intelligence/tools.py)
# Imported explicitly so email's tool list resolves regardless of the
# orchestrator's transitive import graph.
import entities.bill.intelligence.tools  # noqa: F401
import entities.contract_labor.intelligence.tools  # noqa: F401

# Build.One is the single delegation target. Its package registers
# `delegate_to_buildone_orchestrator` (and imports every entity specialist),
# so importing it here guarantees the tool resolves even when email_specialist
# is loaded standalone (e.g. dry-run scripts). At app startup app.py imports
# buildone first; this import is then a cached no-op.
import intelligence.agents.buildone  # noqa: F401

# Register the agent itself. No per-specialist delegation tools are registered
# here anymore — email_specialist's only delegate is
# `delegate_to_buildone_orchestrator` (registered in the buildone package).
from intelligence.agents.email_specialist.definition import email_specialist  # noqa: F401
