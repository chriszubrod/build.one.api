from __future__ import annotations

from typing import Any, Optional
from core.ai.agents.base.state import BaseAgentState


class EmailClassificationState(BaseAgentState):
    """
    State for the email classification agent.

    Input fields are populated before the agent runs.
    Output fields are set by the submit_classification tool.
    Thread fields are populated by the pre-flight header detection
    step before the heuristic or agent runs.
    """

    # ------------------------------------------------------------------
    # Email content inputs (unchanged from original)
    # ------------------------------------------------------------------
    subject:        str
    from_email:     str
    body:           str
    attachments:    list[dict]   # [{name, content_type}, ...]

    # ------------------------------------------------------------------
    # Classification outputs (set by submit_classification tool)
    # ------------------------------------------------------------------
    classification: Optional[str]
    confidence:     float
    reasoning:      str
    signals:        list[Any]

    # ------------------------------------------------------------------
    # Thread awareness (populated by pre-flight before heuristic runs)
    # ------------------------------------------------------------------
    is_reply:               bool        # derived from subject Re: prefix
    is_forward:             bool        # derived from subject Fw:/Fwd: prefix
    internet_message_id:    Optional[str]  # RFC 2822 Message-ID header
    thread_id:              Optional[str]  # EmailThread.PublicId if found
    thread_stage:           Optional[str]  # EmailThread.CurrentStage if found


def initial_state() -> dict:
    """
    Factory for a blank EmailClassificationState.
    Thread fields default to safe values — pre-flight will populate them
    before the heuristic or agent runs.
    """
    return {
        # Email content
        "subject":              "",
        "from_email":           "",
        "body":                 "",
        "attachments":          [],

        # Classification outputs
        "classification":       None,
        "confidence":           0.0,
        "reasoning":            "",
        "signals":              [],

        # Thread awareness
        "is_reply":             False,
        "is_forward":           False,
        "internet_message_id":  None,
        "thread_id":            None,
        "thread_stage":         None,
    }
