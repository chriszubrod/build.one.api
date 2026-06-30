"""Time Tracking specialist — flag-only review of iOS-submitted TimeEntries."""
from pathlib import Path

from intelligence.agents.base import Agent
from intelligence.loop.termination import BudgetPolicy
from intelligence.registry import agents as agent_registry


_PROMPT = (Path(__file__).parent / "prompt.md").read_text()


time_tracking_specialist = Agent(
    name="time_tracking_specialist",
    system_prompt=_PROMPT,
    tools=(
        "validate_time_entry_completeness",
        "flag_time_entry_for_human_review",
    ),
    model="claude-haiku-4-5-20251001",
    provider="cascade",
    credentials_key="time_tracking_agent",
    # Each run is exactly 2 tool calls (validate → flag) + final text.
    # 6 turns leaves slack for retries / clarifications. 60K tokens is
    # well under the bill_specialist budget — TimeEntry shape is tight.
    budget=BudgetPolicy(max_turns=6, max_tokens=60_000),
    description=(
        "Specialist for auto-reviewing iOS-submitted TimeEntries. Runs the "
        "deterministic completeness checklist, maps the resulting reason "
        "codes to a ReviewPriority bucket, and stamps the entry for the "
        "human Approver. Flag-only — never transitions CurrentStatus."
    ),
)


agent_registry.register(time_tracking_specialist)
