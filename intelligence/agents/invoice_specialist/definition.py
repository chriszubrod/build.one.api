"""Invoice specialist agent — customer invoices tied to projects."""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


invoice_specialist = Agent(
    name="invoice_specialist",
    system_prompt=_PROMPT,
    tools=(
        # Invoice — search-only reads + draft create + parent update +
        # workflow action. Line item CRUD (including the polymorphic
        # "select billable items" workflow) is a separate future tool set.
        "search_invoices",
        "read_invoice_by_public_id",
        "create_invoice",
        "update_invoice",
        "delete_invoice",
        "complete_invoice",
        # Project read tools — for parent name resolution / lookup-by-name
        "search_projects",
        "read_project_by_public_id",
        "read_projects_by_customer_id",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="invoice_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description=(
        "Specialist for Invoices (customer invoices tied to projects) "
        "— search/read + draft create + approval-gated update / delete "
        "/ complete workflow. No line-item edits in v1."
    ),
)


agent_registry.register(invoice_specialist)
