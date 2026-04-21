# Python Standard Library Imports
import logging
import time

# Third-party Imports
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from decimal import Decimal

# Local Imports
from entities.expense.api.schemas import ExpenseCreate, ExpenseUpdate
from entities.expense.business.service import ExpenseService
from shared.api.responses import list_response, item_response, accepted_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "expense"])

# Cache last completion result per expense (TTL 1 hour)
_EXPENSE_COMPLETION_RESULT_CACHE: dict[str, dict] = {}
_EXPENSE_COMPLETION_CACHE_TTL_SEC = 3600


def _clean_expense_completion_cache():
    now = time.time()
    expired = [k for k, v in _EXPENSE_COMPLETION_RESULT_CACHE.items() if v.get("expires_at", 0) < now]
    for k in expired:
        del _EXPENSE_COMPLETION_RESULT_CACHE[k]


@router.post("/create/expense")
def create_expense_router(body: ExpenseCreate, current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_create"))):
    """
    Create a new expense.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
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
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create expense")

    return item_response(result.get("data"))


@router.get("/get/expenses")
def get_expenses_router(current_user: dict = Depends(require_module_api(Modules.EXPENSES))):
    """
    Read all expenses.
    """
    expenses = ExpenseService().read_all()
    return list_response([expense.to_dict() for expense in expenses])


@router.get("/get/expense/by-reference-number-and-vendor")
def get_expense_by_reference_number_and_vendor_router(reference_number: str, vendor_public_id: str, current_user: dict = Depends(require_module_api(Modules.EXPENSES))):
    """
    Read an expense by reference number and vendor public ID.
    """
    expense = ExpenseService().read_by_reference_number_and_vendor_public_id(reference_number=reference_number, vendor_public_id=vendor_public_id)
    if not expense:
        raise_not_found("Expense")
    return item_response(expense.to_dict())


@router.get("/get/expense/{public_id}/completion-result")
def get_expense_completion_result_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.EXPENSES))):
    """
    Return the last completion result for an expense (Build One, SharePoint).
    Used by the list page to show step status after an expense completes in the background.
    In-memory cache only (1 hour TTL). Returns 404 if no result or expired.
    """
    _clean_expense_completion_cache()
    entry = _EXPENSE_COMPLETION_RESULT_CACHE.get(public_id)
    if not entry or entry.get("expires_at", 0) < time.time():
        raise_not_found("Completion result")
    return item_response(entry["result"])


@router.get("/get/expense/{public_id}")
def get_expense_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.EXPENSES))):
    """
    Read an expense by public ID.
    """
    expense = ExpenseService().read_by_public_id(public_id=public_id)
    if not expense:
        raise_not_found("Expense")
    return item_response(expense.to_dict())


@router.put("/update/expense/{public_id}")
def update_expense_by_public_id_router(public_id: str, body: ExpenseUpdate, current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_update"))):
    """
    Update an expense by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "vendor_public_id": body.vendor_public_id,
            "expense_date": body.expense_date,
            "reference_number": body.reference_number,
            "total_amount": Decimal(str(body.total_amount)) if body.total_amount is not None else None,
            "memo": body.memo,
            "is_draft": body.is_draft,
        },
        workflow_type="expense_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update expense")

    return item_response(result.get("data"))


@router.delete("/delete/expense/{public_id}")
def delete_expense_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_delete"))):
    """
    Delete an expense by public ID.

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
        workflow_type="expense_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete expense")

    return item_response(result.get("data"))


def _run_complete_expense(public_id: str) -> None:
    """Background task: run expense completion (finalize, SharePoint)."""
    try:
        result = ExpenseService().complete_expense(public_id=public_id)
        logger.info(
            "Complete expense background result: public_id=%s, status_code=%s, expense_finalized=%s",
            public_id, result.get("status_code"), result.get("expense_finalized"),
        )
        expires_at = time.time() + _EXPENSE_COMPLETION_CACHE_TTL_SEC
        _EXPENSE_COMPLETION_RESULT_CACHE[public_id] = {
            "result": result,
            "expires_at": expires_at,
        }
        if result.get("status_code") >= 400:
            logger.warning("Complete expense failed in background: %s", result.get("message"))
    except Exception as e:
        logger.exception("Complete expense background task failed: public_id=%s", public_id)
        failure_result = {
            "status_code": 500,
            "message": str(e),
            "expense_finalized": False,
            "file_uploads": {},
            "excel_syncs": {},
            "qbo_sync": {},
            "errors": [{"step": "complete_expense", "error": str(e)}],
        }
        expires_at = time.time() + _EXPENSE_COMPLETION_CACHE_TTL_SEC
        _EXPENSE_COMPLETION_RESULT_CACHE[public_id] = {"result": failure_result, "expires_at": expires_at}


@router.post("/complete/expense/{public_id}")
def complete_expense_router(
    public_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.EXPENSES, "can_complete")),
):
    """
    Queue expense completion (finalize, SharePoint). Returns 202 immediately;
    work runs in background. Client can poll GET /api/v1/get/expense/{public_id}/completion-result or use list page banner.
    """
    logger.info("Complete expense API called: public_id=%s (queuing background task)", public_id)
    expense = ExpenseService().read_by_public_id(public_id=public_id)
    if not expense:
        raise_not_found("Expense")
    if not getattr(expense, "is_draft", True):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expense is already completed")
    background_tasks.add_task(_run_complete_expense, public_id)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=accepted_response(public_id, "expense_public_id"),
    )
