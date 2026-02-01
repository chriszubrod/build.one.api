# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from decimal import Decimal

# Local Imports
from entities.expense.api.schemas import ExpenseCreate, ExpenseUpdate
from entities.expense.business.service import ExpenseService
from entities.expense.business.complete_service import ExpenseCompleteService
from entities.auth.business.service import get_current_user_api
from workflows.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "expense"])


@router.post("/create/expense")
def create_expense_router(body: ExpenseCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new expense.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "expense_date": body.expense_date,
            "reference_number": body.reference_number,
            "total_amount": Decimal(str(body.total_amount)) if body.total_amount is not None else None,
            "memo": body.memo,
            "is_draft": body.is_draft if body.is_draft is not None else True,
        },
        workflow_type="expense_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create expense")
        )
    
    return result.get("data")


@router.get("/get/expenses")
def get_expenses_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all expenses.
    """
    expenses = ExpenseService().read_all()
    return [expense.to_dict() for expense in expenses]


@router.get("/get/expense/by-reference-number-and-vendor")
def get_expense_by_reference_number_and_vendor_router(reference_number: str, vendor_public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read an expense by reference number and vendor public ID.
    """
    expense = ExpenseService().read_by_reference_number_and_vendor_public_id(reference_number=reference_number, vendor_public_id=vendor_public_id)
    if expense:
        return expense.to_dict()
    return None


@router.get("/get/expense/{public_id}")
def get_expense_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read an expense by public ID.
    """
    expense = ExpenseService().read_by_public_id(public_id=public_id)
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    return expense.to_dict()


@router.put("/update/expense/{public_id}")
def update_expense_by_public_id_router(public_id: str, body: ExpenseUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update an expense by public ID.
    
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
            "vendor_public_id": body.vendor_public_id,
            "expense_date": body.expense_date,
            "reference_number": body.reference_number,
            "total_amount": float(body.total_amount) if body.total_amount else None,
            "memo": body.memo,
            "is_draft": body.is_draft,
        },
        workflow_type="expense_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update expense")
        )
    
    return result.get("data")


@router.delete("/delete/expense/{public_id}")
def delete_expense_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete an expense by public ID.
    
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
        workflow_type="expense_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete expense")
        )
    
    return result.get("data")


@router.post("/complete/expense/{public_id}")
def complete_expense_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Complete an expense: finalize and upload attachments to module folders.
    """
    service = ExpenseCompleteService()
    result = service.complete_expense(public_id=public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to complete expense")
        )
    
    return result
