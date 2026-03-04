# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

# Local Imports
from entities.invoice_attachment.business.service import InvoiceAttachmentService
from entities.auth.business.service import get_current_user_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "invoice_attachment"])


class InvoiceAttachmentCreate(BaseModel):
    invoice_id: int = Field(description="The invoice ID.")
    attachment_id: int = Field(description="The attachment ID.")


@router.post("/create/invoice_attachment")
def create_invoice_attachment_router(body: InvoiceAttachmentCreate, current_user: dict = Depends(get_current_user_api)):
    try:
        result = InvoiceAttachmentService().create(
            invoice_id=body.invoice_id,
            attachment_id=body.attachment_id,
        )
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/get/invoice_attachments")
def get_invoice_attachments_router(current_user: dict = Depends(get_current_user_api)):
    items = InvoiceAttachmentService().read_all()
    return [item.to_dict() for item in items]


@router.get("/get/invoice_attachment/{public_id}")
def get_invoice_attachment_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    item = InvoiceAttachmentService().read_by_public_id(public_id=public_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice attachment not found")
    return item.to_dict()


@router.get("/get/invoice_attachments/invoice/{invoice_id}")
def get_invoice_attachments_by_invoice_id_router(invoice_id: int, current_user: dict = Depends(get_current_user_api)):
    items = InvoiceAttachmentService().read_by_invoice_id(invoice_id=invoice_id)
    return [item.to_dict() for item in items]


@router.delete("/delete/invoice_attachment/{public_id}")
def delete_invoice_attachment_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    item = InvoiceAttachmentService().delete_by_public_id(public_id=public_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice attachment not found")
    return item.to_dict()
