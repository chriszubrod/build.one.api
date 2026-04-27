"""API for EmailMessage + EmailAttachment.

Read endpoints (paginated list / by public_id / attachments) are gated
on Modules.EMAIL_MESSAGES read.

Two write endpoints back the email agent's tools:
  POST /email-attachments/{public_id}/extract — runs DI on demand
  PATCH /email-messages/{public_id}/outcome — flips ProcessingStatus +
                                              applies Outlook category
Both require can_update on EMAIL_MESSAGES.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from entities.email_message.business.categories import (
    AGENT_AWAITING_APPROVAL,
    AGENT_IRRELEVANT,
    AGENT_NEEDS_REVIEW,
    AGENT_PROCESSED,
)
from entities.email_message.business.service import (
    EmailAttachmentBridgeService,
    EmailAttachmentExtractionService,
    EmailMessageService,
)
from integrations.ms.mail.external import client as mail_client
from shared.api.responses import item_response, list_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "email_message"])


# Outcome → (ProcessingStatus, Outlook category) mapping for the PATCH endpoint.
# Keeps the API contract honest: the caller picks a semantic outcome name
# and the server handles the side effects consistently.
_OUTCOME_MAP = {
    "processed":         ("agent_complete", AGENT_PROCESSED),
    "awaiting_approval": ("awaiting_review", AGENT_AWAITING_APPROVAL),
    "needs_review":      ("awaiting_review", AGENT_NEEDS_REVIEW),
    "irrelevant":        ("irrelevant", AGENT_IRRELEVANT),
}


@router.get("/get/email-messages")
def get_email_messages_router(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    processing_status: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    sort_by: str = Query(default="ReceivedDatetime"),
    sort_direction: str = Query(default="DESC"),
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """
    Paginated read of polled email messages.
    Filter by processing_status (pending | processing | extracted |
    awaiting_review | agent_complete | irrelevant | failed).
    """
    service = EmailMessageService()
    rows = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search,
        processing_status=processing_status,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
    total = service.count(
        search_term=search,
        processing_status=processing_status,
        start_date=start_date,
        end_date=end_date,
    )
    return list_response([r.to_dict() for r in rows], count=total)


@router.get("/get/email-message/{public_id}")
def get_email_message_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """One email by public_id, with its attachment rows attached."""
    service = EmailMessageService()
    email = service.read_by_public_id(public_id=public_id)
    if not email:
        raise_not_found("Email message")
    payload = email.to_dict()
    payload["attachments"] = [a.to_dict() for a in service.list_attachments(email_message_id=email.id)]
    return item_response(payload)


@router.get("/get/email-message/{public_id}/attachments")
def get_email_message_attachments_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES)),
):
    """Attachments for an email — useful for inspecting DI extraction state."""
    service = EmailMessageService()
    email = service.read_by_public_id(public_id=public_id)
    if not email:
        raise_not_found("Email message")
    rows = service.list_attachments(email_message_id=email.id)
    return list_response([r.to_dict() for r in rows])


# ─── Write endpoints (agent uses these via tools) ─────────────────────────


@router.post("/email-attachments/{public_id}/extract")
def extract_email_attachment_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES, "can_update")),
):
    """Run Document Intelligence on a single EmailAttachment.

    Idempotent: if the attachment was already extracted, the extraction
    service overwrites with the latest run. Returns the hoisted result
    + validation outcome, suitable for the agent to reason over.
    """
    return item_response(
        EmailAttachmentExtractionService().extract_by_public_id(public_id)
    )


@router.post("/email-attachments/{public_id}/bridge-to-attachment")
def bridge_email_attachment_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES, "can_update")),
):
    """Bridge an EmailAttachment into a regular Attachment row.

    The new Attachment shares the same blob URL as the EmailAttachment
    (no blob copy). Hash-based dedup means re-running on the same email
    attachment returns the existing Attachment.

    The email_specialist agent calls this before delegating to
    bill_specialist, since bill_create requires `attachment_public_id`.
    """
    attachment = EmailAttachmentBridgeService().bridge(public_id)
    return item_response(attachment.to_dict())


class _OutcomeBody(BaseModel):
    outcome: str = Field(
        description=(
            "One of: processed | awaiting_approval | needs_review | irrelevant. "
            "Drives both the DB ProcessingStatus and the Outlook category "
            "applied back to the source message."
        ),
    )
    reason: Optional[str] = Field(
        default=None,
        description=(
            "Optional human-readable note recorded on the EmailMessage row "
            "(LastError column when reason is non-empty)."
        ),
    )
    agent_session_id: Optional[int] = Field(
        default=None,
        description="Optional AgentSession.Id linking this email to its run.",
    )


@router.patch("/email-messages/{public_id}/outcome")
def set_email_outcome_router(
    public_id: str,
    body: _OutcomeBody,
    current_user: dict = Depends(require_module_api(Modules.EMAIL_MESSAGES, "can_update")),
):
    """Mark an email's processing outcome.

    Two side effects, both attempted; either failing doesn't block the
    other:
      1. Update EmailMessage.ProcessingStatus + LastError
      2. PATCH the source MS Graph message to APPEND the outcome category
         (preserves the human-applied input category — we don't strip it)
    """
    if body.outcome not in _OUTCOME_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid outcome '{body.outcome}'. Valid: {list(_OUTCOME_MAP.keys())}",
        )

    service = EmailMessageService()
    email = service.read_by_public_id(public_id=public_id)
    if not email:
        raise_not_found("Email message")

    new_status, outcome_category = _OUTCOME_MAP[body.outcome]

    # 1. DB-side state transition.
    service.update_status(
        id=email.id,
        processing_status=new_status,
        last_error=body.reason,
        agent_session_id=body.agent_session_id,
    )

    # 2. Outlook category — append, don't replace, so the human's input
    #    category (Blue category) stays visible. Best-effort: log + carry
    #    on if Graph PATCH fails, since the DB state has already moved.
    graph_result: dict = {"status": "skipped"}
    if email.graph_message_id and email.mailbox_address:
        existing = list(_safe_categories_from_graph(email.graph_message_id, email.mailbox_address))
        # Avoid stamping the same outcome twice on re-runs.
        if outcome_category not in existing:
            new_categories = existing + [outcome_category]
            graph_result = mail_client.set_categories(
                message_id=email.graph_message_id,
                categories=new_categories,
                mailbox=email.mailbox_address,
            )
        else:
            graph_result = {"status": "already_tagged"}

    return item_response({
        "public_id": public_id,
        "processing_status": new_status,
        "outcome_category": outcome_category,
        "graph_result": graph_result,
    })


def _safe_categories_from_graph(graph_message_id: str, mailbox: str) -> list[str]:
    """Best-effort fetch of the message's current categories. On error
    return an empty list so we still attempt to apply the outcome."""
    try:
        full = mail_client.get_message(message_id=graph_message_id, include_body=False, mailbox=mailbox)
    except Exception as e:
        logger.warning(f"Could not read existing categories for {graph_message_id}: {e}")
        return []
    if full.get("status_code") != 200:
        return []
    return (full.get("email") or {}).get("categories") or []
