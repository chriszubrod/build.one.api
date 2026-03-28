from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.tools import tool

from core.ai.agents.email_agent.config import VALID_TYPES
from core.workflow.api.process_engine import EventType
from core.workflow.business.process_registry import (
    get_email_process,
    get_initial_stage,
    is_valid_transition,
    requires_action,
    get_entity_handoff,
)
from entities.email_thread.persistence.repo import EmailThreadRepository
from entities.email_thread.persistence.message_repo import EmailThreadMessageRepository
from entities.email_thread.persistence.stage_history_repo import EmailThreadStageHistoryRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Repositories — instantiated once at module level (stateless, thread-safe)
# ---------------------------------------------------------------------------
_thread_repo         = EmailThreadRepository()
_message_repo        = EmailThreadMessageRepository()
_stage_history_repo  = EmailThreadStageHistoryRepository()


# ---------------------------------------------------------------------------
# Original tools — unchanged
# ---------------------------------------------------------------------------

@tool
def check_sender_override(from_email: str) -> dict[str, Any]:
    """
    Check if there is a user-defined classification override for this sender.
    Always call this first before any other classification step.
    Returns {found, classification_type, match_type, match_value} or {found: False}.
    """
    try:
        from entities.classification_override.business.service import ClassificationOverrideService
        service = ClassificationOverrideService()
        override = service.find_override(from_email)
        if override:
            return {
                "found":               True,
                "classification_type": override.classification_type,
                "match_type":          override.match_type,
                "match_value":         override.match_value,
            }
        return {"found": False}
    except Exception as error:
        logger.warning(f"check_sender_override failed (non-fatal): {error}")
        return {"found": False}


@tool
def lookup_sender_history(from_email: str) -> dict[str, Any]:
    """
    Look up prior classifications for emails from this sender.
    Returns {found, count, history} where each history entry includes
    classification_type, confidence, and optionally user_corrected_from/to.
    """
    try:
        from entities.inbox.persistence.repo import InboxRecordRepository
        repo = InboxRecordRepository()
        records = repo.read_by_sender(from_email, limit=10)
        if not records:
            return {"found": False, "count": 0, "history": []}
        history = []
        for record in records:
            entry: dict[str, Any] = {
                "classification_type": record.classification_type,
                "confidence":          record.classification_confidence,
            }
            if record.user_override_type:
                entry["user_corrected_to"] = record.user_override_type
            history.append(entry)
        return {"found": True, "count": len(history), "history": history}
    except Exception as error:
        logger.warning(f"lookup_sender_history failed (non-fatal): {error}")
        return {"found": False, "count": 0, "history": []}


@tool
def submit_classification(
    classification_type: str,
    confidence: float,
    reasoning: str,
) -> dict[str, Any]:
    """
    Submit the final classification result for this email.
    classification_type must be one of the valid process registry types.
    confidence is clamped to [0.0, 1.0].
    """
    if classification_type not in VALID_TYPES:
        return {
            "success":  False,
            "error":    f"Invalid classification type '{classification_type}'. "
                        f"Must be one of: {sorted(VALID_TYPES)}",
        }
    confidence = max(0.0, min(1.0, float(confidence)))
    return {
        "success":             True,
        "classification_type": classification_type,
        "confidence":          confidence,
        "reasoning":           reasoning,
    }


# ---------------------------------------------------------------------------
# New thread-aware tools
# ---------------------------------------------------------------------------

@tool
def lookup_email_thread(internet_message_id: str) -> dict[str, Any]:
    """
    Check if an EmailThread already exists for this message chain.
    Uses the RFC 2822 Internet Message-ID header for dedup.

    Call this after submit_classification to determine whether to create
    a new thread or advance an existing one.

    Returns:
        found (bool)          — whether a thread exists
        thread_id (str)       — EmailThread.PublicId if found
        process_type (str)    — registered process type if found
        current_stage (str)   — current stage of the thread if found
        entity_handoff (str)  — entity process this thread hands off to
    """
    try:
        thread = _thread_repo.read_by_internet_message_id(internet_message_id)
        if not thread:
            return {"found": False}
        return {
            "found":          True,
            "thread_id":      thread.public_id,
            "process_type":   thread.process_type,
            "current_stage":  thread.current_stage,
            "entity_handoff": get_entity_handoff(thread.process_type),
        }
    except Exception as error:
        logger.warning(f"lookup_email_thread failed (non-fatal): {error}")
        return {"found": False}


@tool
def create_or_advance_thread(
    classification_type: str,
    confidence: float,
    inbox_record_id: int,
    internet_message_id: str,
    subject: str,
    is_reply: bool,
    is_forward: bool,
    sender_role: str,
    existing_thread_id: str = "",
    current_stage: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """
    Create a new EmailThread or advance an existing one after classification.

    For new threads:
        - Creates an EmailThread at the initial stage from the process registry
        - Creates the first EmailThreadMessage
        - Writes the initial EmailThreadStageHistory entry

    For existing threads:
        - Validates the stage transition against the process registry
        - Advances the thread to the next stage
        - Creates a new EmailThreadMessage at the next position
        - Writes a new EmailThreadStageHistory entry

    Returns:
        success (bool)
        thread_id (str)          — PublicId of the created or updated thread
        new_stage (str)          — stage after this operation
        requires_action (bool)   — whether owner action is needed at this stage
        entity_handoff (str)     — entity process type to hand off to, or ""
        error (str)              — present only on failure
    """
    try:
        is_new_thread = not existing_thread_id

        if is_new_thread:
            # --- New thread ---
            if classification_type == "UNKNOWN":
                initial_stage = "RECEIVED"
                process_type  = "UNKNOWN"
            else:
                initial_stage = get_initial_stage(classification_type, registry_type="email")
                process_type  = classification_type

            # --------------------------------------------------
            # Anomaly check — override stage to REVIEW_NEEDED
            # --------------------------------------------------
            LOW_CONFIDENCE_THRESHOLD = 0.6
            anomaly_reason = None

            if (
                classification_type != "UNKNOWN"
                and confidence < LOW_CONFIDENCE_THRESHOLD
            ):
                anomaly_reason = (
                    f"LOW_CONFIDENCE_CLASSIFICATION: confidence {confidence:.2f} "
                    f"is below threshold {LOW_CONFIDENCE_THRESHOLD}. "
                    f"Classification '{classification_type}' requires owner review."
                )
                initial_stage = "REVIEW_NEEDED"

            elif (is_reply or is_forward) and not existing_thread_id:
                anomaly_reason = (
                    f"ORPHANED_REPLY: email is a "
                    f"{'reply' if is_reply else 'forward'} but no parent "
                    f"EmailThread was found. Owner review required."
                )
                initial_stage = "REVIEW_NEEDED"

            thread_public_id = str(uuid.uuid4())

            thread = _thread_repo.upsert(
                public_id=                  thread_public_id,
                inbox_record_id=            inbox_record_id,
                category=                   classification_type,
                process_type=               process_type,
                current_stage=              initial_stage,
                is_reply=                   is_reply,
                is_forward=                 is_forward,
                internet_message_id=        internet_message_id,
                subject=                    subject,
                classification_confidence=  None,
                is_resolved=                False,
                requires_action=            requires_action(
                                                classification_type,
                                                initial_stage,
                                                registry_type="email"
                                            ) if classification_type != "UNKNOWN" else True,
            )

            message = _message_repo.create(
                public_id=                  str(uuid.uuid4()),
                email_thread_id=            thread.id,
                inbox_record_id=            inbox_record_id,
                sender_role=                sender_role,
                message_position=           1,
                is_reply=                   is_reply,
                is_forward=                 is_forward,
                classification=             classification_type,
                classification_confidence=  confidence,
            )

            _stage_history_repo.create(
                public_id=              str(uuid.uuid4()),
                email_thread_id=        thread.id,
                from_stage=             "CREATED",
                to_stage=               initial_stage,
                triggered_by=           EventType.EMAIL_RECEIVED.value,
                email_thread_message_id=message.id,
                notes=                  anomaly_reason or notes or f"New thread created. Classification: {classification_type}",
            )

            new_stage = initial_stage

        else:
            # --- Existing thread — advance stage ---
            thread = _thread_repo.read_by_public_id(existing_thread_id)
            if not thread:
                return {
                    "success": False,
                    "error":   f"EmailThread '{existing_thread_id}' not found.",
                }

            # Determine next stage from process registry
            process_def  = get_email_process(thread.process_type)
            from_stage   = current_stage or thread.current_stage
            allowed      = process_def.get("transitions", {}).get(from_stage, [])

            if not allowed:
                return {
                    "success": False,
                    "error":   f"No valid transitions from stage '{from_stage}' "
                               f"for process '{thread.process_type}'.",
                }

            # Auto-select next stage — first allowed transition
            to_stage = allowed[0]

            if not is_valid_transition(thread.process_type, from_stage, to_stage, registry_type="email"):
                return {
                    "success": False,
                    "error":   f"Transition '{from_stage}' → '{to_stage}' is not "
                               f"permitted for process '{thread.process_type}'.",
                }

            # Count existing messages to determine position
            existing_messages = _message_repo.read_by_thread_id(thread.id)
            next_position     = len(existing_messages) + 1

            message = _message_repo.create(
                public_id=                  str(uuid.uuid4()),
                email_thread_id=            thread.id,
                inbox_record_id=            inbox_record_id,
                sender_role=                sender_role,
                message_position=           next_position,
                is_reply=                   is_reply,
                is_forward=                 is_forward,
                classification=             classification_type,
                classification_confidence=  confidence,
            )

            _stage_history_repo.create(
                public_id=              str(uuid.uuid4()),
                email_thread_id=        thread.id,
                from_stage=             from_stage,
                to_stage=               to_stage,
                triggered_by=           EventType.EMAIL_RECEIVED.value,
                email_thread_message_id=message.id,
                notes=                  notes or f"Stage advanced via incoming email. Position: {next_position}",
            )

            action_required = requires_action(thread.process_type, to_stage, registry_type="email")

            thread = _thread_repo.upsert(
                public_id=       thread.public_id,
                inbox_record_id= thread.inbox_record_id,
                category=        thread.category,
                process_type=    thread.process_type,
                current_stage=   to_stage,
                is_reply=        thread.is_reply,
                is_forward=      thread.is_forward,
                requires_action= action_required,
            )

            new_stage = to_stage

        return {
            "success":        True,
            "thread_id":      thread.public_id,
            "new_stage":      new_stage,
            "requires_action": requires_action(
                                   thread.process_type,
                                   new_stage,
                                   registry_type="email"
                               ) if thread.process_type != "UNKNOWN" else True,
            "entity_handoff": get_entity_handoff(thread.process_type) or "",
        }

    except Exception as error:
        logger.error(f"create_or_advance_thread failed: {error}")
        return {"success": False, "error": str(error)}
