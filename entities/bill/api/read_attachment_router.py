# Python Standard Library Imports
import logging
from typing import Optional
from uuid import UUID

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# Local Imports
from entities.bill.business.read_attachment_service import (
    AttachmentNotReadable,
    run_bill_read_attachment_with_timeout,
)
from entities.bill_intake_prediction.business.service import IntakeSource
from shared.api.responses import item_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "bill"])


class BillReadAttachmentRequest(BaseModel):
    attachment_public_id: UUID
    project_public_id: Optional[UUID] = Field(default=None)
    intake_source: IntakeSource = "pdf_drop"


@router.post("/bill/read-attachment")
async def read_bill_attachment_router(
    body: BillReadAttachmentRequest,
    _: dict = Depends(require_module_api(Modules.BILLS, "can_create")),
):
    try:
        result = await run_bill_read_attachment_with_timeout(
            attachment_public_id=str(body.attachment_public_id),
            project_public_id=body.project_public_id,
            intake_source=body.intake_source,
        )
    except AttachmentNotReadable:
        raise_not_found("Attachment")
    except HTTPException:
        raise
    except Exception as error:
        logger.warning(
            "bill_read_attachment.unexpected err=%s",
            type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="read_attachment_failed") from error

    return item_response(result)
