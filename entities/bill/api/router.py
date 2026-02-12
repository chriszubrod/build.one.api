# Python Standard Library Imports
import logging
import time

# Third-party Imports
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from decimal import Decimal

# Local Imports
from entities.bill.api.schemas import BillCreate, BillUpdate
from entities.bill.business.service import BillService
from entities.bill.persistence.repo import BillRepository
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "bill"])


@router.post("/create/bill")
def create_bill_router(body: BillCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new bill.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "payment_term_public_id": body.payment_term_public_id,
            "bill_date": body.bill_date,
            "due_date": body.due_date,
            "bill_number": body.bill_number,
            "total_amount": Decimal(str(body.total_amount)) if body.total_amount is not None else None,
            "memo": body.memo,
            "is_draft": body.is_draft if body.is_draft is not None else True,
        },
        workflow_type="bill_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create bill")
        )
    
    return result.get("data")


@router.get("/get/bills")
def get_bills_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all bills.
    """
    bills = BillService().read_all()
    return [bill.to_dict() for bill in bills]


@router.get("/get/bill/by-bill-number-and-vendor")
def get_bill_by_bill_number_and_vendor_router(bill_number: str, vendor_public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a bill by bill number and vendor public ID.
    """
    bill = BillService().read_by_bill_number_and_vendor_public_id(bill_number=bill_number, vendor_public_id=vendor_public_id)
    if bill:
        return bill.to_dict()
    return None


def _clean_completion_cache():
    now = time.time()
    expired = [k for k, v in _BILL_COMPLETION_RESULT_CACHE.items() if v.get("expires_at", 0) < now]
    for k in expired:
        del _BILL_COMPLETION_RESULT_CACHE[k]


@router.get("/get/bill/{public_id}/completion-result")
def get_bill_completion_result_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Return the last completion result for a bill (Build One, SharePoint, Excel, QBO).
    Used by the list page to show step status after a bill completes in the background.
    Reads from DB (shared across workers) then in-memory cache. Returns 404 if no result or expired (1 hour TTL).
    """
    # DB is shared across Gunicorn workers; in-memory is per-worker
    result = BillRepository().get_completion_result(public_id)
    if result is not None:
        return result
    _clean_completion_cache()
    entry = _BILL_COMPLETION_RESULT_CACHE.get(public_id)
    if not entry or entry.get("expires_at", 0) < time.time():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No completion result found or expired")
    return entry["result"]


@router.get("/get/bill/{public_id}")
def get_bill_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a bill by public ID.
    """
    bill = BillService().read_by_public_id(public_id=public_id)
    if not bill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
    return bill.to_dict()


@router.put("/update/bill/{public_id}")
def update_bill_by_public_id_router(public_id: str, body: BillUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a bill by public ID.
    If the bill is being completed (is_draft changing to False), attachments will be synced to SharePoint.
    
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
            "payment_term_public_id": body.payment_term_public_id,
            "bill_date": body.bill_date,
            "due_date": body.due_date,
            "bill_number": body.bill_number,
            "total_amount": float(body.total_amount) if body.total_amount else None,
            "memo": body.memo,
            "is_draft": body.is_draft,
        },
        workflow_type="bill_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        err = result.get("error", "Failed to update bill")
        if "concurrency" in err.lower() or "row-version" in err.lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
    
    return result.get("data")


@router.delete("/delete/bill/{public_id}")
def delete_bill_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a bill by public ID.
    
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
        workflow_type="bill_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete bill")
        )
    
    return result.get("data")


# Cache last completion result per bill so list page can show Build One / SharePoint / Excel / QBO status (TTL 1 hour)
_BILL_COMPLETION_RESULT_CACHE: dict[str, dict] = {}
_BILL_COMPLETION_CACHE_TTL_SEC = 3600


def _run_complete_bill(public_id: str) -> None:
    """Background task: run full bill completion (Build One, SharePoint, Excel, QBO)."""
    try:
        result = BillService().complete_bill(public_id=public_id)
        logger.info(
            "Complete bill background result: public_id=%s, status_code=%s, bill_finalized=%s",
            public_id, result.get("status_code"), result.get("bill_finalized"),
        )
        expires_at = time.time() + _BILL_COMPLETION_CACHE_TTL_SEC
        # In-memory for same-worker hits
        _BILL_COMPLETION_RESULT_CACHE[public_id] = {
            "result": result,
            "expires_at": expires_at,
        }
        # DB so list page can read from any worker after redirect
        BillRepository().set_completion_result(public_id, result, expires_at)
        if result.get("status_code") >= 400:
            logger.warning("Complete bill failed in background: %s", result.get("message"))
    except Exception as e:
        logger.exception("Complete bill background task failed: public_id=%s", public_id)


@router.post("/complete/bill/{public_id}")
def complete_bill_router(
    public_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Queue bill completion (finalize, SharePoint, Excel). Returns 202 immediately;
    work runs in background. Client can poll GET /api/v1/get/bill/{public_id} (is_draft) or use list page banner.
    """
    logger.info("Complete bill API called: public_id=%s (queuing background task)", public_id)
    bill_service = BillService()
    bill = bill_service.read_by_public_id(public_id=public_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    if not getattr(bill, "is_draft", True):
        raise HTTPException(status_code=400, detail="Bill is already completed")
    background_tasks.add_task(_run_complete_bill, public_id)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "accepted", "bill_public_id": public_id},
    )
