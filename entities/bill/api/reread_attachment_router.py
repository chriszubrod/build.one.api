# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.bill.business.reread_attachment_service import (
    BillRereadBillNotFoundError,
    BillRereadNoAttachmentError,
    BillRereadNotDraftError,
    reread_draft_bill_attachment,
)
from entities.bill.business.read_attachment_service import AttachmentNotReadable
from shared.access import EntityNotAccessibleError
from shared.api.responses import item_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "bill"])


@router.post("/bill/{public_id}/reread-attachment")
async def reread_bill_attachment_router(
    public_id: str,
    _: dict = Depends(require_module_api(Modules.BILLS, "can_update")),
):
    try:
        result = await reread_draft_bill_attachment(bill_public_id=public_id)
    except EntityNotAccessibleError:
        raise
    except BillRereadBillNotFoundError:
        raise_not_found("Bill")
    except BillRereadNotDraftError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only draft bills can be re-read",
        )
    except BillRereadNoAttachmentError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no attachment")
    except AttachmentNotReadable:
        raise_not_found("Attachment")
    except HTTPException:
        raise
    except Exception as error:
        logger.warning(
            "bill_reread_attachment.unexpected err=%s",
            type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="reread_attachment_failed") from error

    return item_response(result)
