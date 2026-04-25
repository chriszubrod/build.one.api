"""Expense specialist agent — vendor expenses + refunds (Expense.IsCredit=true)."""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


expense_specialist = Agent(
    name="expense_specialist",
    system_prompt=_PROMPT,
    tools=(
        # Expense — search-only reads + draft create + parent update +
        # workflow action. Line item CRUD is a separate future tool set.
        "search_expenses",
        "read_expense_by_public_id",
        "read_expense_by_reference_and_vendor",
        "create_expense",
        "update_expense",
        "delete_expense",
        "complete_expense",
        # Vendor read tools — for parent name resolution and lookup-by-name
        "search_vendors",
        "read_vendor_by_public_id",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="expense_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description=(
        "Specialist for Expenses (and refunds via Expense.IsCredit=true) — "
        "search/read + draft create + approval-gated update / delete / "
        "complete workflow. No line-item edits in v1."
    ),
)


agent_registry.register(expense_specialist)
