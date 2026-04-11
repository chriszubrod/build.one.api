# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from decimal import Decimal

# Local Imports
from entities.bill_credit.api.schemas import BillCreditCreate, BillCreditUpdate
from entities.bill_credit.business.service import BillCreditService
from entities.bill_credit.business.complete_service import BillCreditCompleteService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found

router = APIRouter(prefix="/api/v1", tags=["api", "bill_credit"])


@router.post("/create/bill-credit")
def create_bill_credit_router(body: BillCreditCreate, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_create"))):
    """
    Create a new bill credit.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "credit_date": body.credit_date,
            "credit_number": body.credit_number,
            "total_amount": Decimal(str(body.total_amount)) if body.total_amount is not None else None,
            "memo": body.memo,
            "is_draft": body.is_draft if body.is_draft is not None else True,
        },
        workflow_type="bill_credit_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create bill credit")
    
    return item_response(result.get("data"))


@router.get("/get/bill-credits")
def get_bill_credits_router(current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS))):
    """
    Read all bill credits.
    """
    bill_credits = BillCreditService().read_all()
    return list_response([bill_credit.to_dict() for bill_credit in bill_credits])


@router.get("/get/bill-credit/by-credit-number-and-vendor")
def get_bill_credit_by_credit_number_and_vendor_router(credit_number: str, vendor_public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS))):
    """
    Read a bill credit by credit number and vendor public ID.
    """
    bill_credit = BillCreditService().read_by_credit_number_and_vendor_public_id(credit_number=credit_number, vendor_public_id=vendor_public_id)
    if bill_credit:
        return item_response(bill_credit.to_dict())
    return None


@router.get("/get/bill-credit/{public_id}")
def get_bill_credit_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS))):
    """
    Read a bill credit by public ID.
    """
    bill_credit = BillCreditService().read_by_public_id(public_id=public_id)
    if not bill_credit:
        raise_not_found("Bill credit")
    return item_response(bill_credit.to_dict())


@router.put("/update/bill-credit/{public_id}")
def update_bill_credit_by_public_id_router(public_id: str, body: BillCreditUpdate, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_update"))):
    """
    Update a bill credit by public ID.
    
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
            "credit_date": body.credit_date,
            "credit_number": body.credit_number,
            "total_amount": float(body.total_amount) if body.total_amount else None,
            "memo": body.memo,
            "is_draft": body.is_draft,
        },
        workflow_type="bill_credit_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update bill credit")
    
    return item_response(result.get("data"))


@router.delete("/delete/bill-credit/{public_id}")
def delete_bill_credit_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_delete"))):
    """
    Delete a bill credit by public ID.
    
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
        workflow_type="bill_credit_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete bill credit")
    
    return item_response(result.get("data"))


@router.post("/complete/bill-credit/{public_id}")
def complete_bill_credit_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_complete"))):
    """
    Complete a bill credit: finalize and upload attachments to module folders.
    """
    service = BillCreditCompleteService()
    result = service.complete_bill_credit(public_id=public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to complete bill credit")
        )
    
    return result
