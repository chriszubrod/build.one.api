"""Customer specialist agent — Customer CRUD + Project read."""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


customer_specialist = Agent(
    name="customer_specialist",
    system_prompt=_PROMPT,
    tools=(
        "list_customers",
        "search_customers",
        "read_customer_by_public_id",
        "read_customer_by_id",
        "create_customer",
        "update_customer",
        "delete_customer",
        # Project read tools for child queries / disambiguation
        "list_projects",
        "search_projects",
        "read_project_by_public_id",
        "read_projects_by_customer_id",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="customer_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description="Specialist for Customers (CRUD) and Customer→Projects relationships (read).",
)


agent_registry.register(customer_specialist)
