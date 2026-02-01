# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.expense_line_item_attachment.api.schemas import ExpenseLineItemAttachmentCreate
from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService
from entities.auth.business.service import get_current_user_api
from workflows.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "expense_line_item_attachment"])
service = ExpenseLineItemAttachmentService()


@router.post("/create/expense-line-item-attachment")
def create_expense_line_item_attachment_router(
    body: ExpenseLineItemAttachmentCreate, current_user: dict = Depends(get_current_user_api)
):
    """
    Create a new expense line item attachment.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "expense_line_item_public_id": body.expense_line_item_public_id,
            "attachment_public_id": body.attachment_public_id,
        },
        workflow_type="expense_line_item_attachment_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create expense line item attachment")
        )
    
    return result.get("data")


@router.get("/get/expense-line-item-attachments")
def get_expense_line_item_attachments_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all expense line item attachments.
    """
    try:
        expense_line_item_attachments = service.read_all()
        return [elia.to_dict() for elia in expense_line_item_attachments]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/expense-line-item-attachment/{public_id}")
def get_expense_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(get_current_user_api)
):
    """
    Read an expense line item attachment by public ID.
    """
    try:
        expense_line_item_attachment = service.read_by_public_id(public_id=public_id)
        if not expense_line_item_attachment:
            raise HTTPException(status_code=404, detail="Expense line item attachment not found")
        return expense_line_item_attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/expense-line-item-attachment/by-expense-line-item/{expense_line_item_id}")
def get_expense_line_item_attachment_by_expense_line_item_id_router(
    expense_line_item_id: str, current_user: dict = Depends(get_current_user_api)
):
    """
    Read expense line item attachment by expense line item public ID.
    Returns the single attachment for the expense line item (1-1 relationship).
    """
    try:
        expense_line_item_attachment = service.read_by_expense_line_item_id(expense_line_item_public_id=expense_line_item_id)
        if not expense_line_item_attachment:
            raise HTTPException(status_code=404, detail="Expense line item attachment not found")
        return expense_line_item_attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/expense-line-item-attachment/{public_id}")
def delete_expense_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(get_current_user_api)
):
    """
    Delete an expense line item attachment by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="expense_line_item_attachment_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete expense line item attachment")
        )
    
    return result.get("data")
