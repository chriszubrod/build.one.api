"""Build.One — the central orchestrator agent over the build.one data model.

Build.One takes a request (from a human chat session today, and from any
trigger source — email, scheduler, MCP — via the EntityActionEnvelope once
the routing work lands) and routes it to the right specialist agent,
interprets the result, and produces the final answer. It makes no direct
HTTP calls; every entity operation flows through a delegation. (Renamed
from "Scout" 2026-06-25 as part of the centralized-orchestrator work.)
"""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


buildone = Agent(
    name="buildone",
    system_prompt=_PROMPT,
    tools=(
        "delegate_to_sub_cost_code",
        "delegate_to_cost_code",
        "delegate_to_customer",
        "delegate_to_project",
        "delegate_to_vendor",
        "delegate_to_bill",
        "delegate_to_bill_credit",
        "delegate_to_expense",
        "delegate_to_invoice",
    ),
    model="claude-sonnet-4-6",
    provider="anthropic",
    credentials_key="buildone_agent",
    budget=BudgetPolicy(max_turns=12, max_tokens=150_000),
    description="Central orchestrator — routes requests to entity specialists.",
)


agent_registry.register(buildone)
