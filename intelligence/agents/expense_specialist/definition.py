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
        # workflow action + line-item CRUD.
        "search_expenses",
        "read_expense_by_public_id",
        "read_expense_by_reference_and_vendor",
        "create_expense",
        "update_expense",
        "delete_expense",
        "complete_expense",
        "add_expense_line_items",
        "update_expense_line_item",
        "remove_expense_line_item",
        # Vendor read tools — for parent name resolution and lookup-by-name
        "search_vendors",
        "read_vendor_by_public_id",
        # Ranked multi-strategy vendor lookup for receipt-driven creation
        # (domain -> exact -> abbr -> prefix -> substring). Use this, not
        # search_vendors, when the receipt's vendor name may not match the DB.
        "find_vendor_for_invoice",
        # SubCostCode reads — required to resolve cost-code id for line items
        "search_sub_cost_codes",
        "read_sub_cost_code_by_number",
        "read_sub_cost_code_by_public_id",
        # Project reads — required to resolve project_public_id for line items
        "search_projects",
        "read_project_by_public_id",
        # Resolve a job-site / Ship To address -> Project via the project
        # specialist (find_project_for_invoice), same as bill_specialist.
        "delegate_to_project_specialist",
    ),
    model="claude-sonnet-4-6",
    provider="cascade",
    credentials_key="expense_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description=(
        "Specialist for Expenses (and refunds via Expense.IsCredit=true) — "
        "search/read + receipt-driven draft create (attachment + inline "
        "summary line, ungated) + approval-gated update / delete / complete "
        "workflow + line-item CRUD. Resolves vendor via find_vendor_for_invoice "
        "and job-site project via delegate_to_project_specialist."
    ),
)


agent_registry.register(expense_specialist)
