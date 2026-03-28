from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from core.workflow.api.process_engine import EventType, Channel, ProcessEngine, get_process_engine
from entities.auth.business.service import get_current_user_api
from entities.email_thread.api.schemas import (
    EmailThreadCorrectClassification,
    EmailThreadResponse,
)
from entities.email_thread.business.service import EmailThreadService

logger   = logging.getLogger(__name__)
router   = APIRouter(prefix="/api/v1", tags=["api", "email-thread"])


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@router.get("/get/email-thread/{public_id}")
async def get_email_thread(
    public_id:    str,
    current_user=Depends(get_current_user_api),
):
    """Read a single EmailThread by public ID."""
    service = EmailThreadService()
    thread  = service.read_by_public_id(public_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Email thread not found.")
    return thread.to_dict()


@router.get("/get/email-threads/requiring-action")
async def get_email_threads_requiring_action(
    owner_user_id: Optional[int] = None,
    current_user=Depends(get_current_user_api),
):
    """
    Return all open EmailThreads requiring owner action.
    Optionally filtered by owner_user_id.
    Used to populate the process inbox view.
    """
    service = EmailThreadService()
    threads = service.read_requiring_action(owner_user_id=owner_user_id)
    return [t.to_dict() for t in threads]


# ---------------------------------------------------------------------------
# Correct classification
# ---------------------------------------------------------------------------

@router.post("/correct-classification/{public_id}")
async def correct_email_thread_classification(
    public_id: str,
    body:      EmailThreadCorrectClassification,
    current_user=Depends(get_current_user_api),
):
    """
    Correct the classification of a misclassified EmailThread.

    Resets the thread to its initial stage under the new process type
    and writes an immutable stage history record with USER_ACTION as
    the trigger. The thread is flagged requires_action=True so it
    surfaces in the process inbox for re-processing.

    Returns the updated EmailThread.
    Raises 404 if the thread is not found.
    Raises 422 if the new_classification_type is invalid.
    """
    try:
        service = EmailThreadService()
        thread  = service.correct_classification(
            public_id=               public_id,
            new_classification_type= body.new_classification_type,
            notes=                   body.notes,
            user_id=                 getattr(current_user, "id", None),
        )
        return thread.to_dict()

    except ValueError as error:
        status = 404 if "not found" in str(error).lower() else 422
        raise HTTPException(status_code=status, detail=str(error))

    except Exception as error:
        logger.error(f"Error correcting email thread classification: {error}")
        raise HTTPException(status_code=500, detail="Internal server error.")
