# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from decimal import Decimal

# Local Imports
from entities.bill.api.schemas import BillCreate, BillUpdate
from entities.bill.business.service import BillService
from entities.bill.persistence.repo import BillRepository
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "bill"])


@router.post("/create/bill")
def create_bill_router(
    body: BillCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_create")),
):
    """
    Create a new bill.

    Routes through the workflow engine for audit logging and state tracking.
    When is_draft=False, triggers background completion (SharePoint, Excel, QBO).
    """
    is_draft = body.is_draft if body.is_draft is not None else True

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
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
            "is_draft": is_draft,
        },
        workflow_type="bill_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create bill")
        )

    data = result.get("data")

    # If completing (not draft), check review approval then queue background pipeline
    if not is_draft and data and data.get("public_id"):
        from shared.rbac import is_admin_user
        from entities.review_entry.business.service import ReviewEntryService
        bill = BillService().read_by_public_id(data["public_id"])
        if bill and not is_admin_user(current_user) and not ReviewEntryService().is_approved(bill_id=bill.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bill requires approved review before completion"
            )
        background_tasks.add_task(_run_complete_bill, data["public_id"])
        import json
        serializable = json.loads(json.dumps(data, default=str))
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=serializable,
        )

    return data


@router.get("/get/bills")
def get_bills_router(current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Read all bills.
    """
    bills = BillService().read_all()
    return [bill.to_dict() for bill in bills]


@router.get("/get/bill/by-bill-number-and-vendor")
def get_bill_by_bill_number_and_vendor_router(bill_number: str, vendor_public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Read a bill by bill number and vendor public ID.
    """
    bill = BillService().read_by_bill_number_and_vendor_public_id(bill_number=bill_number, vendor_public_id=vendor_public_id)
    if bill:
        return bill.to_dict()
    return None


@router.get("/get/bill/{public_id}/completion-result")
def get_bill_completion_result_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Return the completion result for a bill (Build One, SharePoint, Excel, QBO).
    """
    result = BillRepository().get_completion_result(public_id)
    if result is not None:
        return result
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No completion result found")


@router.get("/get/bill/{public_id}")
def get_bill_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Read a bill by public ID.
    """
    bill = BillService().read_by_public_id(public_id=public_id)
    if not bill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
    return bill.to_dict()


@router.get("/get/bill/id/{id}")
def get_bill_by_id_router(id: int, current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Read a bill by ID.
    """
    bill = BillService().read_by_id(id=id)
    if not bill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
    return bill.to_dict()


@router.put("/update/bill/{public_id}")
def update_bill_by_public_id_router(
    public_id: str,
    body: BillUpdate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_update")),
):
    """
    Update a bill by public ID.
    When is_draft transitions from True to False, triggers background completion
    (SharePoint, Excel, QBO).
    """
    # Check if this is a draft-to-complete transition
    is_completing = body.is_draft is False
    if is_completing:
        bill = BillService().read_by_public_id(public_id=public_id)
        if bill and not getattr(bill, "is_draft", True):
            is_completing = False  # Already completed, don't re-trigger

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
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
            "total_amount": Decimal(str(body.total_amount)) if body.total_amount else None,
            "memo": body.memo,
            "is_draft": body.is_draft,
        },
        workflow_type="bill_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        err = result.get("error", "Failed to update bill")
        if "concurrency" in err.lower() or "row-version" in err.lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)

    data = result.get("data")

    # If completing, queue background pipeline
    if is_completing:
        background_tasks.add_task(_run_complete_bill, public_id)
        # Convert Decimals to strings for JSON serialization
        import json
        serializable = json.loads(json.dumps(data, default=str))
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=serializable,
        )

    return data


@router.delete("/delete/bill/{public_id}")
def delete_bill_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS, "can_delete"))):
    """
    Delete a bill by public ID.
    
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
        workflow_type="bill_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete bill")
        )
    
    return result.get("data")


def _run_complete_bill(public_id: str) -> None:
    """Background task: run full bill completion (Build One, SharePoint, Excel, QBO)."""
    try:
        result = BillService().complete_bill(public_id=public_id)
        logger.info(
            "Complete bill background result: public_id=%s, status_code=%s, bill_finalized=%s",
            public_id, result.get("status_code"), result.get("bill_finalized"),
        )
        BillRepository().set_completion_result(public_id, result)
        logger.info("Completion result saved for bill %s (status_code=%s)", public_id, result.get("status_code"))
        if result.get("status_code") >= 400:
            logger.warning("Complete bill failed in background: %s", result.get("message"))
    except Exception as e:
        logger.exception("Complete bill background task failed: public_id=%s", public_id)


@router.post("/complete/bill/{public_id}")
def complete_bill_router(
    public_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_module_api(Modules.BILLS, "can_complete")),
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

    # Review approval gate (admin users bypass)
    from shared.rbac import is_admin_user
    from entities.review_entry.business.service import ReviewEntryService
    if not is_admin_user(current_user) and not ReviewEntryService().is_approved(bill_id=bill.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bill requires approved review before completion"
        )

    background_tasks.add_task(_run_complete_bill, public_id)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "accepted", "bill_public_id": public_id},
    )
