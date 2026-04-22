"""Scout — read-only Q&A agent over the build.one data model.

Scout's tool set grows methodically: one entity at a time, colocated with
the entity (entities/{name}/intelligence/tools.py). Today: SubCostCode.
"""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


scout = Agent(
    name="scout",
    system_prompt=_PROMPT,
    tools=(
        "list_sub_cost_codes",
        "search_sub_cost_codes",
        "read_sub_cost_code_by_public_id",
        "read_sub_cost_code_by_number",
        "read_sub_cost_code_by_alias",
        "read_cost_code_by_id",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="scout_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description="Read-only Q&A assistant. Today: sub-cost-codes only.",
)


agent_registry.register(scout)
