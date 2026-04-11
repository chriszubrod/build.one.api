# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.invoice_line_item_attachment.api.schemas import InvoiceLineItemAttachmentCreate
from entities.invoice_line_item_attachment.business.service import InvoiceLineItemAttachmentService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.api.responses import list_response, item_response, raise_not_found

router = APIRouter(prefix="/api/v1", tags=["api", "invoice_line_item_attachment"])
service = InvoiceLineItemAttachmentService()


@router.post("/create/invoice-line-item-attachment")
def create_invoice_line_item_attachment_router(
    body: InvoiceLineItemAttachmentCreate, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_create"))
):
    try:
        result = service.create(
            tenant_id=current_user.get("tenant_id", 1),
            invoice_line_item_public_id=body.invoice_line_item_public_id,
            attachment_public_id=body.attachment_public_id,
        )
        return item_response(result.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/invoice-line-item-attachments")
def get_invoice_line_item_attachments_router(current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))):
    try:
        attachments = service.read_all()
        return list_response([a.to_dict() for a in attachments])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/invoice-line-item-attachment/{public_id}")
def get_invoice_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))
):
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise_not_found("Invoice line item attachment")
        return item_response(attachment.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/invoice-line-item-attachment/by-invoice-line-item/{invoice_line_item_id}")
def get_invoice_line_item_attachments_by_invoice_line_item_id_router(
    invoice_line_item_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))
):
    try:
        attachments = service.read_by_invoice_line_item_id(invoice_line_item_public_id=invoice_line_item_id)
        return list_response([a.to_dict() for a in attachments])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/invoice-line-item-attachment/{public_id}")
def delete_invoice_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_delete"))
):
    try:
        result = service.delete_by_public_id(
            public_id=public_id,
            tenant_id=current_user.get("tenant_id", 1),
        )
        if not result:
            raise_not_found("Invoice line item attachment")
        return item_response(result.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
