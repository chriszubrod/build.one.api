"""Project specialist agent — Project CRUD + Customer read."""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


project_specialist = Agent(
    name="project_specialist",
    system_prompt=_PROMPT,
    tools=(
        "list_projects",
        "search_projects",
        "read_project_by_public_id",
        "read_projects_by_customer_id",
        "create_project",
        "update_project",
        "delete_project",
        # Customer read tools for parent resolution
        "search_customers",
        "read_customer_by_public_id",
        "read_customer_by_id",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="project_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description="Specialist for Projects (CRUD) and Project→Customer relationships (read).",
)


agent_registry.register(project_specialist)
