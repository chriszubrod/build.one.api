# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from decimal import Decimal

# Local Imports
from modules.bill.api.schemas import BillCreate, BillUpdate
from modules.bill.business.service import BillService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "bill"])


@router.post("/create/bill")
def create_bill_router(body: BillCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new bill.
    """
    try:
        bill = BillService().create(
            vendor_public_id=body.vendor_public_id,
            terms_id=body.terms_id,
            bill_date=body.bill_date,
            due_date=body.due_date,
            bill_number=body.bill_number,
            total_amount=Decimal(str(body.total_amount)) if body.total_amount is not None else None,
            memo=body.memo,
            is_draft=body.is_draft if body.is_draft is not None else True,
        )
        return bill.to_dict()
    except ValueError as e:
        # Handle duplicate bill number error gracefully
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


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
    """
    bill = BillService().update_by_public_id(public_id=public_id, bill=body)
    if not bill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
    return bill.to_dict()


@router.delete("/delete/bill/{public_id}")
def delete_bill_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a bill by public ID.
    """
    bill = BillService().delete_by_public_id(public_id=public_id)
    if not bill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill not found")
    return bill.to_dict()
