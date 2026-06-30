"""Contract Labor specialist — processes forwarded worker timesheet
emails into draft ContractLabor rows."""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


contract_labor_specialist = Agent(
    name="contract_labor_specialist",
    system_prompt=_PROMPT,
    tools=(
        # Vendor binding via sender email.
        "find_contract_labor_vendor_by_email",
        # Project resolution from job-site address.
        "delegate_to_project_specialist",
        # ContractLabor row creation (timesheet-intake path).
        "create_contract_labor",
        # Reviewer-reply detection (Unit 2): bind a PM/Owner reply to
        # its (ContractLabor, Project) pair via ConversationId.
        "find_contract_labor_by_conversation_id",
        # Reviewer-reply apply (Unit 3): SCC resolution + insert a
        # Review row with the PM's decision. find_sub_cost_code_for_reply
        # is shared with bill_specialist; reuse the existing registration.
        "find_sub_cost_code_for_reply",
        "apply_contract_labor_reviewer_decision",
    ),
    model="claude-haiku-4-5-20251001",
    provider="cascade",
    credentials_key="contract_labor_agent",
    # Each run does ~3 tool calls (lookup, delegate, create) + final
    # text. 8 turns leaves slack for retries / clarifications.
    budget=BudgetPolicy(max_turns=8, max_tokens=80_000),
    description=(
        "Specialist for forwarded worker timesheet emails. Parses the "
        "email, binds the sender to a contract-labor Vendor, resolves "
        "the job-site address to a Project, and creates a draft "
        "ContractLabor row (status='pending_review') for human review "
        "to add rate / markup / SubCostCode before billing."
    ),
)


agent_registry.register(contract_labor_specialist)
