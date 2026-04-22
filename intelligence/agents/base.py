"""Agent definition — pure declarative data.

An Agent declares WHAT it is (name, prompt, tools, model, credentials) —
not HOW to run. The loop/session_runner/run_agent orchestrator consumes
these instances; adding a new agent is a matter of building an Agent
dataclass and registering it.
"""
from dataclasses import dataclass, field
from typing import Optional

from intelligence.loop.termination import BudgetPolicy


@dataclass(frozen=True)
class Agent:
    name: str
    system_prompt: str
    tools: tuple[str, ...]          # tool names; resolved via tools.registry
    model: str
    provider: str                    # "anthropic", etc. — keyed into transport.registry
    credentials_key: str             # config key prefix for this agent's login creds
    budget: BudgetPolicy = field(default_factory=BudgetPolicy)
    description: Optional[str] = None  # optional — shown in admin/debug UIs
