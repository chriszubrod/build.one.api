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
        # Invoice CRUD + workflow
        "search_invoices",
        "read_invoice_by_public_id",
        "create_invoice",
        "update_invoice",
        "delete_invoice",
        "complete_invoice",
        # Packet workflow (V2)
        "get_billable_items_for_invoice",
        "get_next_invoice_number",
        "reconcile_invoice",
        "add_invoice_line_items",
        "update_invoice_line_item",
        "remove_invoice_line_item",
        "generate_invoice_packet",
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
