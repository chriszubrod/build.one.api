# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.expense_line_item_attachment.api.schemas import ExpenseLineItemAttachmentCreate
from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found

router = APIRouter(prefix="/api/v1", tags=["api", "expense_line_item_attachment"])
service = ExpenseLineItemAttachmentService()


@router.post("/create/expense-line-item-attachment")
def create_expense_line_item_attachment_router(
    body: ExpenseLineItemAttachmentCreate, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_create"))
):
    """
    Create a new expense line item attachment.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "expense_line_item_public_id": body.expense_line_item_public_id,
            "attachment_public_id": body.attachment_public_id,
        },
        workflow_type="expense_line_item_attachment_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create expense line item attachment")
    
    return item_response(result.get("data"))


@router.get("/get/expense-line-item-attachments")
def get_expense_line_item_attachments_router(current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))):
    """
    Read all expense line item attachments.
    """
    try:
        expense_line_item_attachments = service.read_all()
        return list_response([elia.to_dict() for elia in expense_line_item_attachments])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/expense-line-item-attachment/{public_id}")
def get_expense_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))
):
    """
    Read an expense line item attachment by public ID.
    """
    try:
        expense_line_item_attachment = service.read_by_public_id(public_id=public_id)
        if not expense_line_item_attachment:
            raise_not_found("Expense line item attachment")
        return item_response(expense_line_item_attachment.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/expense-line-item-attachment/by-expense-line-item/{expense_line_item_id}")
def get_expense_line_item_attachment_by_expense_line_item_id_router(
    expense_line_item_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))
):
    """
    Read expense line item attachment by expense line item public ID.
    Returns the single attachment for the expense line item (1-1 relationship).
    """
    try:
        expense_line_item_attachment = service.read_by_expense_line_item_id(expense_line_item_public_id=expense_line_item_id)
        if not expense_line_item_attachment:
            raise_not_found("Expense line item attachment")
        return item_response(expense_line_item_attachment.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/expense-line-item-attachment/{public_id}")
def delete_expense_line_item_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_delete"))
):
    """
    Delete an expense line item attachment by public ID.
    
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
        workflow_type="expense_line_item_attachment_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete expense line item attachment")
    
    return item_response(result.get("data"))
