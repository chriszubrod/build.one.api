"""Read-only API for EmailMessage + EmailAttachment (Phase 1.1).

All routes gate on Modules.DASHBOARD for v1 — any logged-in user with
dashboard access can read the polled inbox. A dedicated EMAIL_MESSAGES
module + per-role grants will arrive when the email agent ships in
Phase 2.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from entities.email_message.business.service import EmailMessageService
from shared.api.responses import item_response, list_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "email_message"])


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
    current_user: dict = Depends(require_module_api(Modules.DASHBOARD)),
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
    return list_response([r.to_dict() for r in rows], total=total, page=page, page_size=page_size)


@router.get("/get/email-message/{public_id}")
def get_email_message_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.DASHBOARD)),
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
    current_user: dict = Depends(require_module_api(Modules.DASHBOARD)),
):
    """Attachments for an email — useful for inspecting DI extraction state."""
    service = EmailMessageService()
    email = service.read_by_public_id(public_id=public_id)
    if not email:
        raise_not_found("Email message")
    rows = service.list_attachments(email_message_id=email.id)
    return list_response([r.to_dict() for r in rows])
