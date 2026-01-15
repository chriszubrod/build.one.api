# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends
from decimal import Decimal

# Local Imports
from modules.bill_line_item.api.schemas import BillLineItemCreate, BillLineItemUpdate
from modules.bill_line_item.business.service import BillLineItemService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "bill_line_item"])


@router.post("/create/bill_line_item")
def create_bill_line_item_router(body: BillLineItemCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new bill line item.
    """
    bill_line_item = BillLineItemService().create(
        bill_public_id=body.bill_public_id,
        sub_cost_code_id=body.sub_cost_code_id,
        project_public_id=body.project_public_id,
        description=body.description,
        quantity=body.quantity,
        rate=Decimal(str(body.rate)) if body.rate is not None else None,
        amount=Decimal(str(body.amount)) if body.amount is not None else None,
        is_billable=body.is_billable,
        is_billed=body.is_billed,
        markup=Decimal(str(body.markup)) if body.markup is not None else None,
        price=Decimal(str(body.price)) if body.price is not None else None,
        is_draft=body.is_draft if body.is_draft is not None else True,
    )
    return bill_line_item.to_dict()


@router.get("/get/bill_line_items")
def get_bill_line_items_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all bill line items.
    """
    bill_line_items = BillLineItemService().read_all()
    return [bill_line_item.to_dict() for bill_line_item in bill_line_items]


@router.get("/get/bill_line_item/{public_id}")
def get_bill_line_item_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a bill line item by public ID.
    """
    bill_line_item = BillLineItemService().read_by_public_id(public_id=public_id)
    return bill_line_item.to_dict()


@router.get("/get/bill_line_items/bill/{bill_id}")
def get_bill_line_items_by_bill_id_router(bill_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read all bill line items for a specific bill.
    """
    bill_line_items = BillLineItemService().read_by_bill_id(bill_id=bill_id)
    return [bill_line_item.to_dict() for bill_line_item in bill_line_items]


@router.put("/update/bill_line_item/{public_id}")
def update_bill_line_item_by_public_id_router(public_id: str, body: BillLineItemUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a bill line item by public ID.
    """
    bill_line_item = BillLineItemService().update_by_public_id(public_id=public_id, bill_line_item=body)
    return bill_line_item.to_dict()


@router.delete("/delete/bill_line_item/{public_id}")
def delete_bill_line_item_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a bill line item by public ID.
    """
    bill_line_item = BillLineItemService().delete_by_public_id(public_id=public_id)
    return bill_line_item.to_dict()
