# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from decimal import Decimal

# Local Imports
from entities.invoice_line_item.api.schemas import InvoiceLineItemCreate, InvoiceLineItemUpdate
from entities.invoice_line_item.business.service import InvoiceLineItemService
from entities.auth.business.service import get_current_user_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "invoice_line_item"])


@router.post("/create/invoice_line_item")
def create_invoice_line_item_router(body: InvoiceLineItemCreate, current_user: dict = Depends(get_current_user_api)):
    try:
        line_item = InvoiceLineItemService().create(
            tenant_id=current_user.get("tenant_id", 1),
            invoice_public_id=body.invoice_public_id,
            source_type=body.source_type,
            bill_line_item_id=body.bill_line_item_id,
            expense_line_item_id=body.expense_line_item_id,
            bill_credit_line_item_id=body.bill_credit_line_item_id,
            description=body.description,
            amount=Decimal(str(body.amount)) if body.amount is not None else None,
            markup=Decimal(str(body.markup)) if body.markup is not None else None,
            price=Decimal(str(body.price)) if body.price is not None else None,
            is_draft=body.is_draft if body.is_draft is not None else True,
        )
        return line_item.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/get/invoice_line_items")
def get_invoice_line_items_router(current_user: dict = Depends(get_current_user_api)):
    items = InvoiceLineItemService().read_all()
    return [item.to_dict() for item in items]


@router.get("/get/invoice_line_item/{public_id}")
def get_invoice_line_item_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    item = InvoiceLineItemService().read_by_public_id(public_id=public_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice line item not found")
    return item.to_dict()


@router.get("/get/invoice_line_items/invoice/{invoice_id}")
def get_invoice_line_items_by_invoice_id_router(invoice_id: int, current_user: dict = Depends(get_current_user_api)):
    items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice_id)
    return [item.to_dict() for item in items]


@router.put("/update/invoice_line_item/{public_id}")
def update_invoice_line_item_by_public_id_router(public_id: str, body: InvoiceLineItemUpdate, current_user: dict = Depends(get_current_user_api)):
    try:
        item = InvoiceLineItemService().update_by_public_id(
            public_id=public_id,
            row_version=body.row_version,
            invoice_public_id=body.invoice_public_id,
            source_type=body.source_type,
            bill_line_item_id=body.bill_line_item_id,
            expense_line_item_id=body.expense_line_item_id,
            bill_credit_line_item_id=body.bill_credit_line_item_id,
            description=body.description,
            amount=float(body.amount) if body.amount else None,
            markup=float(body.markup) if body.markup else None,
            price=float(body.price) if body.price else None,
            is_draft=body.is_draft,
        )
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice line item not found")
        return item.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/delete/invoice_line_item/{public_id}")
def delete_invoice_line_item_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    item = InvoiceLineItemService().delete_by_public_id(public_id=public_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice line item not found")
    return item.to_dict()
