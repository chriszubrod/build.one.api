"""Bill specialist agent — search, read, parent-only updates, complete workflow."""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


bill_specialist = Agent(
    name="bill_specialist",
    system_prompt=_PROMPT,
    tools=(
        # Bill — search-only reads + create-draft + parent-field updates +
        # workflow action + line-item CRUD.
        "search_bills",
        "read_bill_by_public_id",
        "read_bill_by_number_and_vendor",
        "create_bill",
        "update_bill",
        "delete_bill",
        "complete_bill",
        "add_bill_line_items",
        "update_bill_line_item",
        "remove_bill_line_item",
        # Reviewer-reply path (Wave 3): find the linked Bill from a PM's
        # reply conversation, then apply their decision (approval +
        # SCC + description, or rejection with comments).
        "find_bill_by_conversation_id",
        "apply_reviewer_decision",
        # Vendor read tools — for parent name resolution and lookup-by-name
        "search_vendors",
        "find_vendor_for_invoice",
        "read_vendor_by_public_id",
        # SubCostCode reads — required to resolve cost-code id for line items
        "search_sub_cost_codes",
        "read_sub_cost_code_by_number",
        "read_sub_cost_code_by_public_id",
        "find_sub_cost_code_for_reply",
        # Project reads — required to resolve project_public_id for line items
        "search_projects",
        "read_project_by_public_id",
        # Project resolution for invoice-driven creates: delegate to
        # project_specialist with the Ship To address.
        "delegate_to_project_specialist",
    ),
    model="claude-sonnet-4-6",
    provider="cascade",
    credentials_key="bill_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description=(
        "Specialist for Bills — search/read + draft create + approval-"
        "gated update / delete / complete workflow + line-item CRUD."
    ),
)


agent_registry.register(bill_specialist)
