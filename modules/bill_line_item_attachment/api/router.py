# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from modules.bill_line_item_attachment.api.schemas import BillLineItemAttachmentCreate
from modules.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from modules.auth.business.service import get_current_user_api as get_current_bill_line_item_attachment_api

router = APIRouter(prefix="/api/v1", tags=["api", "bill_line_item_attachment"])
service = BillLineItemAttachmentService()


@router.post("/create/bill-line-item-attachment")
def create_bill_line_item_attachment_router(
    body: BillLineItemAttachmentCreate, current_user: dict = Depends(get_current_bill_line_item_attachment_api)
):
    """
    Create a new bill line item attachment.
    """
    try:
        bill_line_item_attachment = service.create(
            bill_line_item_public_id=body.bill_line_item_public_id,
            attachment_public_id=body.attachment_public_id,
        )
        return bill_line_item_attachment.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/bill-line-item-attachments")
def get_bill_line_item_attachments_router(current_user: dict = Depends(get_current_bill_line_item_attachment_api)):
    """
    Read all bill line item attachments.
    """
    try:
        bill_line_item_attachments = service.read_all()
        return [blia.to_dict() for blia in bill_line_item_attachments]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/bill-line-item-attachment/{public_id}")
def get_bill_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(get_current_bill_line_item_attachment_api)
):
    """
    Read a bill line item attachment by public ID.
    """
    try:
        bill_line_item_attachment = service.read_by_public_id(public_id=public_id)
        if not bill_line_item_attachment:
            raise HTTPException(status_code=404, detail="Bill line item attachment not found")
        return bill_line_item_attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/bill-line-item-attachment/by-bill-line-item/{bill_line_item_id}")
def get_bill_line_item_attachment_by_bill_line_item_id_router(
    bill_line_item_id: str, current_user: dict = Depends(get_current_bill_line_item_attachment_api)
):
    """
    Read bill line item attachment by bill line item public ID.
    Returns the single attachment for the bill line item (1-1 relationship).
    """
    try:
        bill_line_item_attachment = service.read_by_bill_line_item_id(bill_line_item_public_id=bill_line_item_id)
        if not bill_line_item_attachment:
            raise HTTPException(status_code=404, detail="Bill line item attachment not found")
        return bill_line_item_attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/bill-line-item-attachment/{public_id}")
def delete_bill_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(get_current_bill_line_item_attachment_api)
):
    """
    Delete a bill line item attachment by public ID.
    """
    try:
        bill_line_item_attachment = service.delete_by_public_id(public_id=public_id)
        if not bill_line_item_attachment:
            raise HTTPException(status_code=404, detail="Bill line item attachment not found")
        return bill_line_item_attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
