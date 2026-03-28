"""
Email Classification Agent State
"""
from __future__ import annotations

from typing import Optional

from core.ai.agents.base.state import BaseAgentState


class EmailClassificationState(BaseAgentState):
    """
    State for the email classification agent.

    Extends BaseAgentState with email-specific fields.
    """
    subject: str
    from_email: str
    body: str
    attachments: list[dict]  # [{name, content_type}]

    # Output fields (set by submit_classification tool)
    classification: Optional[str]
    confidence: float
    reasoning: str
    signals: list[str]


def initial_state(
    tenant_id: int,
    subject: str = "",
    from_email: str = "",
    body: str = "",
    attachments: list = None,
) -> EmailClassificationState:
    """Create initial state for email classification."""
    return EmailClassificationState(
        messages=[],
        llm_calls=0,
        tenant_id=tenant_id,
        agent_run_id=None,
        user_id=None,
        errors=[],
        mode="interactive",
        subject=subject,
        from_email=from_email,
        body=body,
        attachments=attachments or [],
        classification=None,
        confidence=0.0,
        reasoning="",
        signals=[],
    )
