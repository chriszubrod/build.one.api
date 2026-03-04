"""
Contract Labor Matching Agent State
"""
from __future__ import annotations

from typing import Optional

from core.ai.agents.base.state import BaseAgentState


class ContractLaborMatchState(BaseAgentState):
    """State for the contract labor matching agent."""
    import_batch_id: str
    entries_to_match: list[dict]
    current_entry_index: int
    matches_proposed: list[dict]
    unresolved: list[dict]
    entries_matched: int
    entries_skipped: int


def initial_state(
    tenant_id: int,
    import_batch_id: str = "",
    entries: list = None,
) -> ContractLaborMatchState:
    return ContractLaborMatchState(
        messages=[],
        llm_calls=0,
        tenant_id=tenant_id,
        agent_run_id=None,
        user_id=None,
        errors=[],
        mode="batch",
        import_batch_id=import_batch_id,
        entries_to_match=entries or [],
        current_entry_index=0,
        matches_proposed=[],
        unresolved=[],
        entries_matched=0,
        entries_skipped=0,
    )
