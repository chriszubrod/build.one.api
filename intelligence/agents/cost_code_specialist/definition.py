"""CostCode specialist agent — handles catalog + relationships.

Read-only for now; writes come when we build the full CostCode CRUD
tool set. Credentials and role grants (CostCode CRUD + SubCostCode
read) are already provisioned on `cost_code_agent`.
"""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


cost_code_specialist = Agent(
    name="cost_code_specialist",
    system_prompt=_PROMPT,
    tools=(
        "list_cost_codes",
        "read_cost_code_by_id",
        "read_cost_code_by_public_id",
        "search_sub_cost_codes",
        "read_sub_cost_code_by_public_id",
        "create_cost_code",
        "update_cost_code",
        "delete_cost_code",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="cost_code_agent",
    budget=BudgetPolicy(max_turns=10, max_tokens=120_000),
    description="Specialist for CostCode catalog + SubCostCode relationships.",
)


agent_registry.register(cost_code_specialist)
