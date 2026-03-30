# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from decimal import Decimal

# Local Imports
from entities.bill_credit_line_item.api.schemas import BillCreditLineItemCreate, BillCreditLineItemUpdate
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "bill_credit_line_item"])


@router.post("/create/bill-credit-line-item")
def create_bill_credit_line_item_router(body: BillCreditLineItemCreate, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_create"))):
    """
    Create a new bill credit line item.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "bill_credit_public_id": body.bill_credit_public_id,
            "sub_cost_code_id": body.sub_cost_code_id,
            "project_public_id": body.project_public_id,
            "description": body.description,
            "quantity": float(body.quantity) if body.quantity is not None else None,
            "unit_price": float(body.unit_price) if body.unit_price is not None else None,
            "amount": float(body.amount) if body.amount is not None else None,
            "is_billable": body.is_billable,
            "is_billed": body.is_billed,
            "billable_amount": float(body.billable_amount) if body.billable_amount is not None else None,
            "is_draft": body.is_draft if body.is_draft is not None else True,
        },
        workflow_type="bill_credit_line_item_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create bill credit line item")
        )
    
    return result.get("data")


@router.get("/get/bill-credit-line-items")
def get_bill_credit_line_items_router(current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS))):
    """
    Read all bill credit line items.
    """
    line_items = BillCreditLineItemService().read_all()
    return [item.to_dict() for item in line_items]


@router.get("/get/bill-credit-line-item/{public_id}")
def get_bill_credit_line_item_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS))):
    """
    Read a bill credit line item by public ID.
    """
    line_item = BillCreditLineItemService().read_by_public_id(public_id=public_id)
    if not line_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill credit line item not found")
    return line_item.to_dict()


@router.get("/get/bill-credit-line-items/by-bill-credit/{bill_credit_public_id}")
def get_bill_credit_line_items_by_bill_credit_router(bill_credit_public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS))):
    """
    Read all bill credit line items for a specific bill credit.
    """
    from entities.bill_credit.business.service import BillCreditService
    
    bill_credit = BillCreditService().read_by_public_id(public_id=bill_credit_public_id)
    if not bill_credit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bill credit not found")
    
    line_items = BillCreditLineItemService().read_by_bill_credit_id(bill_credit_id=bill_credit.id)
    return [item.to_dict() for item in line_items]


@router.put("/update/bill-credit-line-item/{public_id}")
def update_bill_credit_line_item_by_public_id_router(public_id: str, body: BillCreditLineItemUpdate, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_update"))):
    """
    Update a bill credit line item by public ID.
    
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
            "bill_credit_public_id": body.bill_credit_public_id,
            "sub_cost_code_id": body.sub_cost_code_id,
            "project_public_id": body.project_public_id,
            "description": body.description,
            "quantity": float(body.quantity) if body.quantity is not None else None,
            "unit_price": float(body.unit_price) if body.unit_price is not None else None,
            "amount": float(body.amount) if body.amount is not None else None,
            "is_billable": body.is_billable,
            "is_billed": body.is_billed,
            "billable_amount": float(body.billable_amount) if body.billable_amount is not None else None,
            "is_draft": body.is_draft,
        },
        workflow_type="bill_credit_line_item_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update bill credit line item")
        )
    
    return result.get("data")


@router.delete("/delete/bill-credit-line-item/{public_id}")
def delete_bill_credit_line_item_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILL_CREDITS, "can_delete"))):
    """
    Delete a bill credit line item by public ID.
    
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
        workflow_type="bill_credit_line_item_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete bill credit line item")
        )
    
    return result.get("data")
