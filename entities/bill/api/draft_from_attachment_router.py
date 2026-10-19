# Python Standard Library Imports
import logging
from uuid import UUID

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

# Local Imports
from entities.bill.business.draft_from_attachment_service import draft_bill_from_attachment
from entities.bill.business.read_attachment_service import AttachmentNotReadable
from shared.api.responses import item_response, raise_not_found
from shared.api.auth_user import resolve_user_id
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "bill"])


class BillDraftFromAttachmentRequest(BaseModel):
    attachment_public_id: UUID


@router.post("/bill/draft-from-attachment")
async def draft_bill_from_attachment_router(
    body: BillDraftFromAttachmentRequest,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_create")),
):
    try:
        result = await draft_bill_from_attachment(
            attachment_public_id=str(body.attachment_public_id),
            user_id=resolve_user_id(current_user),
            tenant_id=current_user.get("tenant_id", 1),
        )
    except AttachmentNotReadable:
        raise_not_found("Attachment")
    except Exception as error:
        logger.warning(
            "bill_draft_from_attachment.unexpected err=%s",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=500, detail="draft_from_attachment_failed"
        ) from error

    return item_response(result)
