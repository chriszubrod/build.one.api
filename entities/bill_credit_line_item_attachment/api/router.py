# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.bill_credit_line_item_attachment.api.schemas import BillCreditLineItemAttachmentCreate
from entities.bill_credit_line_item_attachment.business.service import BillCreditLineItemAttachmentService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "bill_credit_line_item_attachment"])


@router.post("/create/bill-credit-line-item-attachment")
def create_bill_credit_line_item_attachment_router(body: BillCreditLineItemAttachmentCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new bill credit line item attachment link.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "bill_credit_line_item_public_id": body.bill_credit_line_item_public_id,
            "attachment_public_id": body.attachment_public_id,
        },
        workflow_type="bill_credit_line_item_attachment_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create bill credit line item attachment")
        )
    
    return result.get("data")


@router.get("/get/bill-credit-line-item-attachments")
def get_bill_credit_line_item_attachments_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all bill credit line item attachments.
    """
    attachments = BillCreditLineItemAttachmentService().read_all()
    return [attachment.to_dict() for attachment in attachments]


@router.get("/get/bill-credit-line-item-attachment/{public_id}")
def get_bill_credit_line_item_attachment_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a bill credit line item attachment by public ID.
    """
    attachment = BillCreditLineItemAttachmentService().read_by_public_id(public_id=public_id)
    if not attachment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill credit line item attachment not found")
    return attachment.to_dict()


@router.get("/get/bill-credit-line-item-attachment/by-line-item/{bill_credit_line_item_public_id}")
def get_bill_credit_line_item_attachment_by_line_item_router(bill_credit_line_item_public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a bill credit line item attachment by bill credit line item public ID.
    Returns the single attachment for the bill credit line item (1-1 relationship).
    """
    attachment = BillCreditLineItemAttachmentService().read_by_bill_credit_line_item_id(
        bill_credit_line_item_public_id=bill_credit_line_item_public_id
    )
    if not attachment:
        return None
    return attachment.to_dict()


@router.delete("/delete/bill-credit-line-item-attachment/{public_id}")
def delete_bill_credit_line_item_attachment_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a bill credit line item attachment by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="bill_credit_line_item_attachment_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete bill credit line item attachment")
        )
    
    return result.get("data")
