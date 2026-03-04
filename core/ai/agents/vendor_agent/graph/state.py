"""
VendorAgent State Definition

Defines the state that flows through the LangGraph agent.
Extends BaseAgentState with vendor-specific processing fields.
"""
from __future__ import annotations

from typing import Optional

from core.ai.agents.base.state import BaseAgentState, base_initial_state


class VendorAgentState(BaseAgentState):
    """
    State for the VendorAgent graph.

    Inherits from BaseAgentState:
        messages, llm_calls, tenant_id, agent_run_id, user_id, errors, mode

    Vendor-specific attributes:
        current_vendor_id: Vendor currently being processed (for single-vendor mode)
        vendors_to_process: Queue of vendor IDs to process (for batch mode)
        vendors_processed: Count of vendors processed
        proposals_created: Count of proposals created
        skipped_count: Count of vendors skipped
    """
    current_vendor_id: Optional[int]
    vendors_to_process: list[int]
    vendors_processed: int
    proposals_created: int
    skipped_count: int


def initial_state(
    tenant_id: int,
    agent_run_id: int = None,
    user_id: str = None,
    mode: str = "batch",
    vendor_id: int = None,
) -> VendorAgentState:
    """
    Create initial state for a new agent run.

    Args:
        tenant_id: Tenant context
        agent_run_id: ID of the agent run record
        user_id: User who triggered the run
        mode: 'batch' for processing multiple vendors, 'single' for one vendor
        vendor_id: Specific vendor ID (required for 'single' mode)

    Returns:
        Initialized VendorAgentState
    """
    return VendorAgentState(
        **base_initial_state(
            tenant_id=tenant_id,
            agent_run_id=agent_run_id,
            user_id=user_id,
            mode=mode,
        ),
        current_vendor_id=vendor_id if mode == "single" else None,
        vendors_to_process=[vendor_id] if vendor_id and mode == "single" else [],
        vendors_processed=0,
        proposals_created=0,
        skipped_count=0,
    )
