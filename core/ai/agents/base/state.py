"""
Base Agent State

Defines the common state fields shared by all LangGraph agents.
Individual agents extend this with domain-specific fields via TypedDict inheritance.
"""
from __future__ import annotations

import operator
from typing import Annotated, Optional

from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage


class BaseAgentState(TypedDict):
    """
    Base state shared by all LangGraph agents.

    Attributes:
        messages: Accumulated conversation messages (appended via operator.add)
        llm_calls: Counter for LLM invocations (safety limit)
        tenant_id: Tenant context for multi-tenancy
        agent_run_id: ID of the current agent run (for tracking)
        user_id: User who triggered the run
        errors: List of error messages encountered
        mode: Operating mode - 'interactive', 'batch', or 'headless'
    """
    messages: Annotated[list[AnyMessage], operator.add]
    llm_calls: int
    tenant_id: int
    agent_run_id: Optional[int]
    user_id: Optional[str]
    errors: list[str]
    mode: str


def base_initial_state(
    tenant_id: int,
    agent_run_id: int = None,
    user_id: str = None,
    mode: str = "interactive",
) -> dict:
    """
    Create the base fields for initial state. Agents should merge this
    with their own domain-specific initial values.

    Usage:
        state = {**base_initial_state(tenant_id=1), "my_field": "value"}
    """
    return {
        "messages": [],
        "llm_calls": 0,
        "tenant_id": tenant_id,
        "agent_run_id": agent_run_id,
        "user_id": user_id,
        "errors": [],
        "mode": mode,
    }
