"""Agent registry — name → Agent lookup.

Agents register themselves at import time. Callers resolve by name when
invoking an agent via run_agent().
"""
from typing import Optional

from intelligence.agents.base import Agent


_agents: dict[str, Agent] = {}


def register(agent: Agent) -> None:
    if agent.name in _agents:
        raise ValueError(f"Agent already registered: {agent.name!r}")
    _agents[agent.name] = agent


def get(name: str) -> Optional[Agent]:
    return _agents.get(name)


def all_agents() -> list[Agent]:
    return list(_agents.values())


def clear() -> None:
    """Primarily for tests."""
    _agents.clear()
