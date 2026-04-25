"""Vendor specialist agent — Vendor CRUD with soft-delete."""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


vendor_specialist = Agent(
    name="vendor_specialist",
    system_prompt=_PROMPT,
    tools=(
        "search_vendors",
        "read_vendor_by_public_id",
        "create_vendor",
        "update_vendor",
        "delete_vendor",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="vendor_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description="Specialist for Vendors — search-only reads + approval-gated CRUD (soft delete).",
)


agent_registry.register(vendor_specialist)
