# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from decimal import Decimal

# Local Imports
from entities.bill_line_item.api.schemas import BillLineItemCreate, BillLineItemUpdate
from entities.bill_line_item.business.service import BillLineItemService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "bill_line_item"])


@router.post("/create/bill_line_item")
def create_bill_line_item_router(body: BillLineItemCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new bill line item.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "bill_public_id": body.bill_public_id,
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
        workflow_type="bill_line_item_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create bill line item")
        )
    
    return result.get("data")


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
            "bill_public_id": body.bill_public_id,
            "sub_cost_code_id": body.sub_cost_code_id,
            "project_public_id": body.project_public_id,
            "description": body.description,
            "quantity": body.quantity,
            "rate": Decimal(str(body.rate)) if body.rate else None,
            "amount": Decimal(str(body.amount)) if body.amount else None,
            "is_billable": body.is_billable,
            "is_billed": body.is_billed,
            "markup": Decimal(str(body.markup)) if body.markup else None,
            "price": Decimal(str(body.price)) if body.price else None,
            "is_draft": body.is_draft,
        },
        workflow_type="bill_line_item_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update bill line item")
        )
    
    return result.get("data")


@router.delete("/delete/bill_line_item/{public_id}")
def delete_bill_line_item_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a bill line item by public ID.
    
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
        workflow_type="bill_line_item_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete bill line item")
        )
    
    return result.get("data")
