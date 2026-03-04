"""
Invoice Composition Agent State
"""
from __future__ import annotations

from typing import Optional

from core.ai.agents.base.state import BaseAgentState


class InvoiceCompositionState(BaseAgentState):
    """State for the invoice composition agent."""
    project_public_id: str
    billable_items: list[dict]
    proposed_groups: list[dict]
    invoice_draft: Optional[dict]


def initial_state(
    tenant_id: int,
    project_public_id: str = "",
) -> InvoiceCompositionState:
    return InvoiceCompositionState(
        messages=[],
        llm_calls=0,
        tenant_id=tenant_id,
        agent_run_id=None,
        user_id=None,
        errors=[],
        mode="interactive",
        project_public_id=project_public_id,
        billable_items=[],
        proposed_groups=[],
        invoice_draft=None,
    )
