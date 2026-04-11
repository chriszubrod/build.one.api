# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.customer.api.schemas import CustomerCreate, CustomerUpdate
from entities.customer.business.service import CustomerService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "customer"])


@router.post("/create/customer")
def create_customer_router(body: CustomerCreate, current_user: dict = Depends(require_module_api(Modules.CUSTOMERS, "can_create"))):
    """
    Create a new customer.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "email": body.email,
            "phone": body.phone,
        },
        workflow_type="customer_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create customer")
    
    return item_response(result.get("data"))


@router.get("/get/customers")
def get_customers_router(current_user: dict = Depends(require_module_api(Modules.CUSTOMERS))):
    """
    Read all customers.
    """
    customers = CustomerService().read_all()
    return list_response([customer.to_dict() for customer in customers])


@router.get("/get/customer/{public_id}")
def get_customer_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.CUSTOMERS))):
    """
    Read a customer by public ID.
    """
    customer = CustomerService().read_by_public_id(public_id=public_id)
    return item_response(customer.to_dict())


@router.put("/update/customer/{public_id}")
def update_customer_by_public_id_router(public_id: str, body: CustomerUpdate, current_user: dict = Depends(require_module_api(Modules.CUSTOMERS, "can_update"))):
    """
    Update a customer by public ID.
    
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
            "email": body.email,
            "phone": body.phone,
        },
        workflow_type="customer_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update customer")
    
    return item_response(result.get("data"))


@router.delete("/delete/customer/{public_id}")
def delete_customer_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.CUSTOMERS, "can_delete"))):
    """
    Delete a customer by public ID.
    
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
        workflow_type="customer_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete customer")
    
    return item_response(result.get("data"))
