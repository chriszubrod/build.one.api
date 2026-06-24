"""Email specialist — pure orchestrator for the polled invoice inbox."""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


email_specialist = Agent(
    name="email_specialist",
    system_prompt=_PROMPT,
    tools=(
        # Email-message-side tools — internal bookkeeping.
        "read_email_message",
        "search_email_sender_history",
        "extract_email_attachment",
        "record_extracted_fields",
        "bridge_email_attachment",
        "mark_email_outcome",
        # Reviewer-reply detection (Wave 3 + Wave 4): bind an inbound reply
        # to its tracked Bill (Step 1b) or ContractLabor (Step 1bx)
        # conversation before running the standard 3-signal flow. These
        # are lookup-only — bill_specialist / contract_labor_specialist
        # own the apply writes.
        "find_bill_by_conversation_id",
        "find_contract_labor_by_conversation_id",
        # Delegation tools. (bill_credit still deferred.)
        "delegate_to_bill_specialist",
        "delegate_to_contract_labor_specialist",
        "delegate_to_expense_specialist",
    ),
    model="claude-haiku-4-5-20251001",
    provider="anthropic",
    credentials_key="email_agent",
    # Bigger budget than other specialists — a multi-attachment email
    # with DI extraction + delegation can run several minutes and pile
    # up tokens.
    budget=BudgetPolicy(max_turns=20, max_tokens=300_000),
    description=(
        "Specialist for polled invoice-inbox emails. Reads each pending "
        "email, classifies it, runs DI on document attachments, bridges to "
        "Attachment rows, and delegates draft creation to bill_specialist "
        "(vendor invoices) or expense_specialist (point-of-sale receipts). "
        "Stamps an outcome category back to Outlook."
    ),
)


agent_registry.register(email_specialist)
