"""
Agent Tool Context

Thread-local-like context for passing tenant/run info to tool functions
without threading it through every tool parameter.
"""
from __future__ import annotations

from typing import Optional


class AgentToolContext:
    """
    Holds tenant/run context for agent tools.

    Set at the start of each graph run via setup_context node.
    Tools read from class attributes during execution.
    """
    tenant_id: int = 1
    agent_run_id: Optional[int] = None
    user_id: Optional[str] = None
    extra: dict = {}

    @classmethod
    def set(cls, tenant_id: int, agent_run_id: int = None, user_id: str = None, **kwargs):
        cls.tenant_id = tenant_id
        cls.agent_run_id = agent_run_id
        cls.user_id = user_id
        cls.extra = kwargs

    @classmethod
    def reset(cls):
        cls.tenant_id = 1
        cls.agent_run_id = None
        cls.user_id = None
        cls.extra = {}
