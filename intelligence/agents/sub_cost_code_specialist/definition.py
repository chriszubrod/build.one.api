"""SubCostCode specialist agent definition.

Invoked by other agents (today: scout) via the `delegate_to_sub_cost_code`
tool. Carries its own User+Auth+Role with permissions narrowed to
SubCostCode + CostCode modules only.
"""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


sub_cost_code_specialist = Agent(
    name="sub_cost_code_specialist",
    system_prompt=_PROMPT,
    tools=(
        "list_sub_cost_codes",
        "search_sub_cost_codes",
        "read_sub_cost_code_by_public_id",
        "read_sub_cost_code_by_number",
        "read_sub_cost_code_by_alias",
        "read_cost_code_by_id",
        "create_sub_cost_code",
        "update_sub_cost_code",
        "delete_sub_cost_code",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="sub_cost_code_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description="Specialist for sub-cost-codes — read + approval-gated writes.",
)


agent_registry.register(sub_cost_code_specialist)
