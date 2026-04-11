# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.payment_term.api.schemas import PaymentTermCreate, PaymentTermUpdate
from entities.payment_term.business.service import PaymentTermService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found

router = APIRouter(prefix="/api/v1", tags=["api", "payment-term"])
service = PaymentTermService()


@router.post("/create/payment-term")
def create_payment_term_router(body: PaymentTermCreate, current_user: dict = Depends(require_module_api(Modules.BILLS, "can_create"))):
    """
    Create a new payment term.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "description": body.description,
            "discount_percent": body.discount_percent,
            "discount_days": body.discount_days,
            "due_days": body.due_days,
        },
        workflow_type="payment_term_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create payment term")
    
    return item_response(result.get("data"))


@router.get("/get/payment-terms")
def get_payment_terms_router(current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Read all payment terms.
    """
    try:
        payment_terms = service.read_all()
        return list_response([payment_term.to_dict() for payment_term in payment_terms])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/payment-term/{public_id}")
def get_payment_term_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS))):
    """
    Read a payment term by public ID.
    """
    try:
        payment_term = service.read_by_public_id(public_id=public_id)
        if not payment_term:
            raise_not_found("Payment term")
        return item_response(payment_term.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/payment-term/{public_id}")
def update_payment_term_by_public_id_router(public_id: str, body: PaymentTermUpdate, current_user: dict = Depends(require_module_api(Modules.BILLS, "can_update"))):
    """
    Update a payment term by public ID.
    
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
            "name": body.name,
            "description": body.description,
            "discount_percent": body.discount_percent,
            "discount_days": body.discount_days,
            "due_days": body.due_days,
        },
        workflow_type="payment_term_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update payment term")
    
    return item_response(result.get("data"))


@router.delete("/delete/payment-term/{public_id}")
def delete_payment_term_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.BILLS, "can_delete"))):
    """
    Delete a payment term by public ID.
    
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
        workflow_type="payment_term_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete payment term")
    
    return item_response(result.get("data"))
