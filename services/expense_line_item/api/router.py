# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from decimal import Decimal

# Local Imports
from services.expense_line_item.api.schemas import ExpenseLineItemCreate, ExpenseLineItemUpdate
from services.expense_line_item.business.service import ExpenseLineItemService
from services.auth.business.service import get_current_user_api
from workflows.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "expense_line_item"])


@router.post("/create/expense_line_item")
def create_expense_line_item_router(body: ExpenseLineItemCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new expense line item.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "expense_public_id": body.expense_public_id,
            "sub_cost_code_id": body.sub_cost_code_id,
            "project_public_id": body.project_public_id,
            "description": body.description,
            "quantity": body.quantity,
            "rate": Decimal(str(body.rate)) if body.rate is not None else None,
            "amount": Decimal(str(body.amount)) if body.amount is not None else None,
            "is_billable": body.is_billable,
            "is_billed": body.is_billed,
            "markup": Decimal(str(body.markup)) if body.markup is not None else None,
            "price": Decimal(str(body.price)) if body.price is not None else None,
            "is_draft": body.is_draft if body.is_draft is not None else True,
        },
        workflow_type="expense_line_item_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create expense line item")
        )
    
    return result.get("data")


@router.get("/get/expense_line_items")
def get_expense_line_items_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all expense line items.
    """
    expense_line_items = ExpenseLineItemService().read_all()
    return [expense_line_item.to_dict() for expense_line_item in expense_line_items]


@router.get("/get/expense_line_item/{public_id}")
def get_expense_line_item_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read an expense line item by public ID.
    """
    expense_line_item = ExpenseLineItemService().read_by_public_id(public_id=public_id)
    return expense_line_item.to_dict()


@router.get("/get/expense_line_items/expense/{expense_id}")
def get_expense_line_items_by_expense_id_router(expense_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read all expense line items for a specific expense.
    """
    expense_line_items = ExpenseLineItemService().read_by_expense_id(expense_id=expense_id)
    return [expense_line_item.to_dict() for expense_line_item in expense_line_items]


@router.put("/update/expense_line_item/{public_id}")
def update_expense_line_item_by_public_id_router(public_id: str, body: ExpenseLineItemUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update an expense line item by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "expense_public_id": body.expense_public_id,
            "sub_cost_code_id": body.sub_cost_code_id,
            "project_public_id": body.project_public_id,
            "description": body.description,
            "quantity": body.quantity,
            "rate": float(body.rate) if body.rate else None,
            "amount": float(body.amount) if body.amount else None,
            "is_billable": body.is_billable,
            "is_billed": body.is_billed,
            "markup": float(body.markup) if body.markup else None,
            "price": float(body.price) if body.price else None,
            "is_draft": body.is_draft,
        },
        workflow_type="expense_line_item_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update expense line item")
        )
    
    return result.get("data")


@router.delete("/delete/expense_line_item/{public_id}")
def delete_expense_line_item_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete an expense line item by public ID.
    
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
        workflow_type="expense_line_item_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete expense line item")
        )
    
    return result.get("data")
