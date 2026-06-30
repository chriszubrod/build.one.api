"""Email triage specialist — a READ-ONLY, structured-output agent.

The live Phase-3 demo for the model cascade. Given an EmailMessage public_id it
uses read-only tools (read_email_message, search_email_sender_history) to
gather signals, then returns a single JSON classification. Because it only
reads and ends in a structured `{classification, confidence, reason}` answer,
it is safe to RE-RUN — which is exactly what `run_agent_cascade` does as it
escalates a hard email up the cheapest-first ladder.

Its declared model is a placeholder; the cascade overrides provider/model per
rung. Reuses the `email_agent` credentials (already provisioned in prod) — no
new DB user needed.
"""
from intelligence.agents.base import Agent
from intelligence.cascade.email_classification import EMAIL_CLASSIFICATIONS
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_VOCAB = ", ".join(sorted(EMAIL_CLASSIFICATIONS))

_PROMPT = (
    "You are an email triage classifier. You receive a single EmailMessage "
    "public_id. Your job is to read it and decide what kind of email it is.\n\n"
    "Steps:\n"
    "1. Call `read_email_message(public_id)` to load the email (from, subject, "
    "body, attachments).\n"
    "2. **Only if the email's own content leaves the type genuinely ambiguous**, "
    "call `search_email_sender_history(from_email)` ONCE for the sender's prior "
    "pattern. For a clear email — an obvious invoice, statement, receipt, credit "
    "memo, or newsletter — SKIP this call: the email content is enough, and "
    "skipping it is faster and cheaper. Prefer to decide from the email alone.\n"
    "3. Decide on exactly ONE label from this controlled vocabulary:\n"
    f"   {_VOCAB}\n\n"
    "Guidance: vendor_invoice = a vendor billing us; vendor_credit_memo = a "
    "vendor crediting us; vendor_statement = a multi-invoice summary; "
    "vendor_expense_receipt = a point-of-sale/card receipt; "
    "contract_labor_timesheet = a worker timesheet (clock in/out + job-site "
    "address, no invoice); reviewer_reply = an internal PM/Owner approval on a "
    "bill; vendor_newsletter = marketing/FYI; non_actionable = nothing to do; "
    "unknown = you can't tell.\n\n"
    "When you are done, your FINAL message must be ONLY a JSON object — no "
    "prose around it — of the form:\n"
    '{"classification": "<one label from the vocabulary>", '
    '"confidence": <number 0..1>, "reason": "<one short sentence>"}\n'
    "Set confidence to your true calibrated probability that the label is "
    "correct; use a low confidence (and `unknown`) when signals are weak or "
    "conflicting. Do not call any other tools, and never attempt to create, "
    "bridge, delegate, or modify anything — you only read and classify."
)


email_triage_specialist = Agent(
    name="email_triage_specialist",
    system_prompt=_PROMPT,
    tools=(
        "read_email_message",
        "search_email_sender_history",
    ),
    model="claude-haiku-4-5-20251001",   # placeholder; cascade overrides per rung
    provider="cascade",
    credentials_key="email_agent",       # reuse the existing email agent identity
    budget=BudgetPolicy(max_turns=6, max_tokens=60_000),
    description=(
        "Read-only email triage classifier (cascade demo). Reads an email + "
        "sender history and returns a structured classification; safe to "
        "re-run, so the model cascade can escalate it cheapest-first."
    ),
)


agent_registry.register(email_triage_specialist)
