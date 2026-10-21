"""U-704: re-run AI read on a draft bill's existing PDF attachment.

UpsertBillIntakePrediction is keyed on AttachmentId and only updates a row whose BillId IS NULL
and FinalizedDatetime IS NULL (same MERGE guard as pdf_drop). A reread therefore records
intake_source ``reread`` (bound to the bill via the BLIA link) only when the attachment has no
prediction row yet — e.g. drops that returned ``ai_unavailable`` before persisting, or email/manual
attachments never read at create. When the bill already has a bound prediction row for that
attachment, ``record_read`` still runs but the sproc is a no-op and the original row is untouched.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional
from uuid import UUID

from entities.attachment.business.service import AttachmentService
from entities.bill.business.read_attachment_service import (
    read_attachment_link_state,
    run_bill_read_attachment_with_timeout,
)
from entities.bill.business.service import BillService
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.project.business.service import ProjectService

logger = logging.getLogger(__name__)


class BillRereadBillNotFoundError(LookupError):
    """Bill public id does not resolve to an accessible row."""


class BillRereadNotDraftError(Exception):
    """Bill workflow status is not draft; re-read is refused."""


class BillRereadNoAttachmentError(LookupError):
    """Draft bill has no line-item attachment to re-read."""


async def _resolve_attachment_on_draft_bill(
    bill: Any,
    *,
    line_item_service: BillLineItemService,
    blia_service: BillLineItemAttachmentService,
    attachment_service: AttachmentService,
    project_service: ProjectService,
) -> tuple[Any, Optional[UUID]]:
    """Return (attachment, project_public_id) for the lowest-id line with a BLIA link."""
    bli_svc = line_item_service
    blia_svc = blia_service
    att_svc = attachment_service
    proj_svc = project_service

    line_items = await asyncio.to_thread(bli_svc.read_by_bill_id, bill_id=int(bill.id))
    line_items_sorted = sorted(line_items, key=lambda li: int(li.id or 0))
    line_public_ids = [str(li.public_id) for li in line_items_sorted if li.public_id]
    links = await asyncio.to_thread(blia_svc.read_by_bill_line_item_ids, line_public_ids)
    link_by_line_public = {
        str(link.bill_line_item_public_id): link
        for link in links
        if link.bill_line_item_public_id and link.attachment_id is not None
    }

    for line in line_items_sorted:
        if not line.public_id:
            continue
        link = link_by_line_public.get(str(line.public_id))
        if link is None:
            continue
        attachment = await asyncio.to_thread(att_svc.read_by_id, id=int(link.attachment_id))
        if attachment is None or attachment.public_id is None:
            continue

        project_public_id: Optional[UUID] = None
        if line.project_id is not None:
            project = await asyncio.to_thread(proj_svc.read_by_id, id=int(line.project_id))
            if project is not None and project.public_id is not None:
                project_public_id = UUID(str(project.public_id))

        return attachment, project_public_id

    raise BillRereadNoAttachmentError("no attachment")


async def reread_draft_bill_attachment(
    *,
    bill_public_id: str,
    bill_service: Optional[BillService] = None,
    line_item_service: Optional[BillLineItemService] = None,
    blia_service: Optional[BillLineItemAttachmentService] = None,
    attachment_service: Optional[AttachmentService] = None,
    project_service: Optional[ProjectService] = None,
    read_fn: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    read = read_fn or run_bill_read_attachment_with_timeout
    bill_svc = bill_service or BillService()
    bli_svc = line_item_service or BillLineItemService()
    blia_svc = blia_service or BillLineItemAttachmentService()
    att_svc = attachment_service or AttachmentService()
    proj_svc = project_service or ProjectService()

    bill = await asyncio.to_thread(bill_svc.read_by_public_id, public_id=bill_public_id)
    if bill is None or bill.id is None:
        raise BillRereadBillNotFoundError("bill not found")

    if getattr(bill, "status", None) != "draft":
        raise BillRereadNotDraftError("not draft")

    attachment, project_public_id = await _resolve_attachment_on_draft_bill(
        bill,
        line_item_service=bli_svc,
        blia_service=blia_svc,
        attachment_service=att_svc,
        project_service=proj_svc,
    )

    attachment_id = int(attachment.id)
    state = await asyncio.to_thread(read_attachment_link_state, attachment_id)
    is_taxpayer = state.is_taxpayer

    return await read(
        attachment_public_id=str(attachment.public_id),
        project_public_id=project_public_id,
        intake_source="reread",
        pre_gated=(attachment, is_taxpayer),
    )
