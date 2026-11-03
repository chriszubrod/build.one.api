"""Shared body of POST /{bill,expense}/{public_id}/review-replies/sync (U-755).

On-demand copy of reviewer inbox replies for one draft document's recorded review
thread. The entity routers keep their routes, RBAC dependencies and module-level
names; they pass their collaborators in at call time, so the router-module patch
points (`<router>.BillService` / `ExpenseService`, `recorded_review_conversation_ids`,
`MailboxPollService`, `review_reply_item`) and the router's own logger are unchanged.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from fastapi import HTTPException, status

from entities.email_message.business.service import ReviewReplyCopyError
from shared.api.responses import list_response, raise_not_found, raise_workflow_error
from shared.lifecycle.terminal_lock import StatusLockedError


def sync_review_replies(
    *,
    entity_type: str,
    public_id: str,
    read_parent: Callable[[], Any],
    recorded_conversation_ids: Callable[[str, str], Iterable[str]],
    poll_service_factory: Callable[[], Any],
    reply_item: Callable[[Any], dict],
    logger: logging.Logger,
):
    parent = read_parent()
    if parent is None:
        raise_not_found(entity_type)
    if parent.is_draft is not True:
        raise_workflow_error(
            str(StatusLockedError("Review replies can only be synced for a draft document.")),
            "Review reply sync refused",
        )
    conversation_ids = recorded_conversation_ids(entity_type, public_id)
    if not conversation_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="no recorded review thread for this document",
        )
    try:
        messages = poll_service_factory().copy_review_thread_replies(conversation_ids)
    except ReviewReplyCopyError as err:
        logger.warning(
            "copy_review_thread_replies failed status=%s message=%s",
            err.status_code,
            err.message,
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY)
    return list_response([reply_item(m) for m in messages])
