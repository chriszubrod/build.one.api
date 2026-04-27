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
        "extract_email_attachment",
        "bridge_email_attachment",
        "mark_email_outcome",
        # One delegation tool for v1. Add expense / bill_credit later.
        "delegate_to_bill_specialist",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="email_agent",
    # Bigger budget than other specialists — a multi-attachment email
    # with DI extraction + delegation can run several minutes and pile
    # up tokens.
    budget=BudgetPolicy(max_turns=20, max_tokens=300_000),
    description=(
        "Specialist for polled invoice-inbox emails. Reads each pending "
        "email, classifies it, runs DI on invoice-shaped attachments, "
        "bridges to Attachment rows, and delegates draft-bill creation "
        "to bill_specialist. Stamps an outcome category back to Outlook."
    ),
)


agent_registry.register(email_specialist)
