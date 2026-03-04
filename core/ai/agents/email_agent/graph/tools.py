"""
Email Classification Agent Tools
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.tools import tool

from core.ai.agents.base.context import AgentToolContext

logger = logging.getLogger(__name__)

VALID_TYPES = {"bill", "expense", "vendor_credit", "inquiry", "statement", "unknown"}


@tool
def check_sender_override(from_email: str) -> dict:
    """Check if this sender has a pre-configured classification override.

    Returns the override type and match info if found, or indicates no override exists.
    Always call this first before analyzing the email content.
    """
    try:
        from entities.classification_override.business.service import ClassificationOverrideService
        svc = ClassificationOverrideService()
        override = svc.find_override(from_email)
        if override:
            return {
                "found": True,
                "classification_type": override.classification_type,
                "match_type": override.match_type,
                "match_value": override.match_value,
            }
        return {"found": False}
    except Exception as e:
        logger.warning("Override lookup failed: %s", e)
        return {"found": False, "error": str(e)}


@tool
def lookup_sender_history(from_email: str) -> dict:
    """Look up how this sender's previous emails were classified.

    Returns recent classification history for this sender, which is a strong
    signal for how to classify the current email.
    """
    try:
        from entities.inbox.persistence.repo import InboxRecordRepository
        repo = InboxRecordRepository()
        records = repo.read_by_sender(from_email, limit=10)
        if not records:
            return {"found": False, "message": "No prior emails from this sender"}
        history = []
        for r in records:
            entry = {
                "classification_type": r.classification_type,
                "confidence": r.classification_confidence,
            }
            if r.user_override_type:
                entry["user_corrected_from"] = r.user_override_type
                entry["user_corrected_to"] = r.record_type
            history.append(entry)
        return {"found": True, "count": len(history), "history": history}
    except Exception as e:
        logger.warning("Sender history lookup failed: %s", e)
        return {"found": False, "error": str(e)}


@tool
def submit_classification(
    classification_type: str,
    confidence: float,
    reasoning: str,
) -> dict:
    """Submit the final email classification result.

    Args:
        classification_type: One of: bill, expense, vendor_credit, inquiry, statement, unknown
        confidence: Confidence score from 0.0 to 1.0
        reasoning: Brief explanation of why this classification was chosen
    """
    if classification_type not in VALID_TYPES:
        return {"success": False, "error": f"Invalid type '{classification_type}'. Must be one of: {VALID_TYPES}"}
    confidence = max(0.0, min(1.0, confidence))
    return {
        "success": True,
        "classification_type": classification_type,
        "confidence": confidence,
        "reasoning": reasoning,
    }


# All tools for this agent
EMAIL_AGENT_TOOLS = [
    check_sender_override,
    lookup_sender_history,
    submit_classification,
]

TOOLS_BY_NAME = {t.name: t for t in EMAIL_AGENT_TOOLS}
