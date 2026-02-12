"""
VendorAgent State Definition

Defines the state that flows through the LangGraph agent.
"""
from __future__ import annotations

import operator
from typing import Annotated, Optional
from typing_extensions import TypedDict

from langchain_core.messages import AnyMessage


class VendorAgentState(TypedDict):
    """
    State for the VendorAgent graph.

    Attributes:
        messages: Accumulated conversation messages (appended via operator.add)
        llm_calls: Counter for LLM invocations (safety limit)
        tenant_id: Tenant context for multi-tenancy
        agent_run_id: ID of the current agent run (for tracking)
        current_vendor_id: Vendor currently being processed (for single-vendor mode)
        vendors_to_process: Queue of vendor IDs to process (for batch mode)
        vendors_processed: Count of vendors processed
        proposals_created: Count of proposals created
        errors: List of error messages encountered
        mode: Operating mode - 'batch' or 'single'
    """
    # Message accumulator
    messages: Annotated[list[AnyMessage], operator.add]

    # Safety counter
    llm_calls: int

    # Context
    tenant_id: int
    agent_run_id: Optional[int]
    user_id: Optional[str]

    # Processing state
    current_vendor_id: Optional[int]
    vendors_to_process: list[int]
    vendors_processed: int
    proposals_created: int
    skipped_count: int
    errors: list[str]

    # Mode
    mode: str  # 'batch' or 'single'


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
        messages=[],
        llm_calls=0,
        tenant_id=tenant_id,
        agent_run_id=agent_run_id,
        user_id=user_id,
        current_vendor_id=vendor_id if mode == "single" else None,
        vendors_to_process=[vendor_id] if vendor_id and mode == "single" else [],
        vendors_processed=0,
        proposals_created=0,
        skipped_count=0,
        errors=[],
        mode=mode,
    )
