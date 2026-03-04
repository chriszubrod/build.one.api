"""
Copilot Agent State

Extends BaseAgentState with conversation and copilot-specific fields.
"""
from __future__ import annotations

from typing import Optional

from typing_extensions import TypedDict

from core.ai.agents.base.state import BaseAgentState, base_initial_state


class CopilotAgentState(BaseAgentState):
    """State for the conversational copilot agent."""
    conversation_id: Optional[str]
    context: dict
    suggestions: list[str]
    sources: list[dict]


def initial_state(
    tenant_id: int,
    conversation_id: str = None,
    context: dict = None,
) -> dict:
    return {
        **base_initial_state(tenant_id=tenant_id, mode="interactive"),
        "conversation_id": conversation_id,
        "context": context or {},
        "suggestions": [],
        "sources": [],
    }
