"""
Bill Validation Agent State
"""
from __future__ import annotations

from typing import Optional

from core.ai.agents.base.state import BaseAgentState


class BillValidationState(BaseAgentState):
    """State for the bill validation agent."""
    bill_public_id: str
    bill_data: dict
    line_items: list[dict]
    issues_found: list[dict]
    validation_result: Optional[dict]


def initial_state(
    tenant_id: int,
    bill_public_id: str = "",
) -> BillValidationState:
    """Create initial state for bill validation."""
    return BillValidationState(
        messages=[],
        llm_calls=0,
        tenant_id=tenant_id,
        agent_run_id=None,
        user_id=None,
        errors=[],
        mode="interactive",
        bill_public_id=bill_public_id,
        bill_data={},
        line_items=[],
        issues_found=[],
        validation_result=None,
    )
