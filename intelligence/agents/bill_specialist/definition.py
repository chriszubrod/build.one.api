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
        # Bill — search-only reads + parent-field updates + workflow action
        "search_bills",
        "read_bill_by_public_id",
        "read_bill_by_number_and_vendor",
        "update_bill",
        "delete_bill",
        "complete_bill",
        # Vendor read tools — for parent name resolution and lookup-by-name
        "search_vendors",
        "read_vendor_by_public_id",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="bill_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description=(
        "Specialist for Bills — search/read + approval-gated update / "
        "delete / complete workflow. No create or line-item edits in v1."
    ),
)


agent_registry.register(bill_specialist)
