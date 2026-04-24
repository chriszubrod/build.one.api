# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.customer.api.schemas import CustomerCreate, CustomerUpdate
from entities.customer.business.service import CustomerService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import (
    list_response,
    item_response,
    raise_not_found,
    raise_workflow_error,
)

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


@router.get("/get/customer/search")
def search_customers_router(
    q: str,
    limit: int = 10,
    current_user: dict = Depends(require_module_api(Modules.CUSTOMERS)),
):
    """
    Case-insensitive substring search over Customer name (with email +
    phone as secondary match fields). Returns up to `limit` matches
    (default 10). Intended for agent narrow-lookup and dropdown search —
    cheaper than listing the full catalog when only a few rows matter.
    """
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    matches = CustomerService().search_by_name(query=q, limit=limit)
    return list_response([c.to_dict() for c in matches])


@router.get("/get/customer/by-id/{id}")
def get_customer_by_id_router(
    id: int,
    current_user: dict = Depends(require_module_api(Modules.CUSTOMERS)),
):
    """
    Read a customer by internal id (BIGINT).

    Intended for agent tools resolving the Customer referenced by a
    Project.customer_id (FK). Public consumers should use the public_id
    endpoint above — this one exists for server-side identifier
    resolution only.
    """
    customer = CustomerService().read_by_id(id=id)
    if not customer:
        raise_not_found("Customer.")
    return item_response(customer.to_dict())


@router.get("/get/customer/{public_id}")
def get_customer_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.CUSTOMERS))):
    """
    Read a customer by public ID.
    """
    customer = CustomerService().read_by_public_id(public_id=public_id)
    if not customer:
        raise_not_found("Customer.")
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
