"""
Extraction Agent State
"""
from __future__ import annotations

from typing import Optional

from core.ai.agents.base.state import BaseAgentState


class ExtractionAgentState(BaseAgentState):
    """
    State for the extraction agent.

    Extends BaseAgentState with extraction-specific fields.
    """
    # Input
    ocr_content: str
    email_context: dict  # {from_email, subject, filename}

    # Working state
    extracted_fields: dict
    validation_issues: list[str]
    refinement_round: int

    # Output
    final_result: Optional[dict]
    confidence: float


def initial_state(
    tenant_id: int,
    ocr_content: str = "",
    email_context: dict = None,
) -> ExtractionAgentState:
    """Create initial state for extraction."""
    return ExtractionAgentState(
        messages=[],
        llm_calls=0,
        tenant_id=tenant_id,
        agent_run_id=None,
        user_id=None,
        errors=[],
        mode="interactive",
        ocr_content=ocr_content,
        email_context=email_context or {},
        extracted_fields={},
        validation_issues=[],
        refinement_round=0,
        final_result=None,
        confidence=0.0,
    )
