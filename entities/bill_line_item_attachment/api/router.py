# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.bill_line_item_attachment.api.schemas import BillLineItemAttachmentCreate
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import (
    list_response,
    item_response,
    parse_public_id,
    raise_workflow_error,
    raise_not_found,
    raise_server_error,
)

router = APIRouter(prefix="/api/v1", tags=["api", "bill_line_item_attachment"])
service = BillLineItemAttachmentService()


@router.post("/create/bill-line-item-attachment")
def create_bill_line_item_attachment_router(
    body: BillLineItemAttachmentCreate, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_create"))
):
    """
    Create a new bill line item attachment.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    bill_line_item_public_id = parse_public_id(
        body.bill_line_item_public_id, "bill_line_item_public_id"
    )
    attachment_public_id = parse_public_id(body.attachment_public_id, "attachment_public_id")

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "bill_line_item_public_id": bill_line_item_public_id,
            "attachment_public_id": attachment_public_id,
        },
        workflow_type="bill_line_item_attachment_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create bill line item attachment")
    
    return item_response(result.get("data"))


@router.get("/get/bill-line-item-attachments")
def get_bill_line_item_attachments_router(current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))):
    """
    Read all bill line item attachments.
    """
    try:
        bill_line_item_attachments = service.read_all()
        return list_response([blia.to_dict() for blia in bill_line_item_attachments])
    except Exception as error:
        raise_server_error(error, "Failed to read bill line item attachments")


@router.get("/get/bill-line-item-attachment/{public_id}")
def get_bill_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))
):
    """
    Read a bill line item attachment by public ID.
    """
    public_id = parse_public_id(public_id, "public_id")
    try:
        bill_line_item_attachment = service.read_by_public_id(public_id=public_id)
        if not bill_line_item_attachment:
            raise_not_found("Bill line item attachment")
        return item_response(bill_line_item_attachment.to_dict())
    except Exception as error:
        raise_server_error(error, "Failed to read bill line item attachment")


@router.get("/get/bill-line-item-attachment/by-bill-line-item/{bill_line_item_public_id}")
def get_bill_line_item_attachment_by_bill_line_item_public_id_router(
    bill_line_item_public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))
):
    """
    Read the attachment link for a bill line item, by the line item's PUBLIC id.

    At most one link row per BillLineItem -- `UQ_BillLineItemAttachment_BillLineItemId`
    -- hence a single item, not a list. Why that is the right shape, and why the
    many-side (one Attachment across many line items) does not change it:
    tests/test_u411_blia_by_line_item_contract.py.
    """
    bill_line_item_public_id = parse_public_id(bill_line_item_public_id, "bill_line_item_public_id")
    try:
        bill_line_item_attachment = service.read_by_bill_line_item_id(
            bill_line_item_public_id=bill_line_item_public_id
        )
        if not bill_line_item_attachment:
            raise_not_found("Bill line item attachment")
        return item_response(bill_line_item_attachment.to_dict())
    except Exception as error:
        raise_server_error(error, "Failed to read bill line item attachment")


@router.delete("/delete/bill-line-item-attachment/{public_id}")
def delete_bill_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_delete"))
):
    """
    Delete a bill line item attachment by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    public_id = parse_public_id(public_id, "public_id")

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="bill_line_item_attachment_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete bill line item attachment")
    
    return item_response(result.get("data"))
