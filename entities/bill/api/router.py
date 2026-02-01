# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from decimal import Decimal

# Local Imports
from entities.bill.api.schemas import BillCreate, BillUpdate
from entities.bill.business.service import BillService
from entities.bill.business.complete_service import BillCompleteService
from entities.auth.business.service import get_current_user_api
from workflows.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update bill")
        )
    
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


@router.post("/complete/bill/{public_id}")
def complete_bill_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Complete a bill: finalize, upload attachments to module folders, and sync to Excel workbooks.
    """
    print(f"\n{'='*60}")
    print(f"=== COMPLETE BILL API CALLED: {public_id} ===")
    print(f"{'='*60}")
    
    service = BillCompleteService()
    result = service.complete_bill(public_id=public_id)
    
    print(f"\n--- COMPLETE BILL RESULT ---")
    print(f"  status_code: {result.get('status_code')}")
    print(f"  bill_finalized: {result.get('bill_finalized')}")
    print(f"  file_uploads: {result.get('file_uploads')}")
    print(f"  excel_syncs: {result.get('excel_syncs')}")
    qbo_sync = result.get('qbo_sync', {})
    if qbo_sync:
        qbo_status = "✓" if qbo_sync.get('success') else "✗"
        qbo_bill_id = qbo_sync.get('qbo_bill_id')
        print(f"  qbo_sync: {qbo_status} {qbo_sync.get('message', '')} (QBO Bill ID: {qbo_bill_id})")
    if result.get('errors'):
        print(f"  ERRORS: {result.get('errors')}")
    print(f"{'='*60}\n")
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to complete bill")
        )
    
    return result
