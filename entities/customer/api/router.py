# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.customer.api.schemas import CustomerCreate, CustomerUpdate
from entities.customer.business.service import CustomerService
from entities.auth.business.service import get_current_user_api
from workflows.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "customer"])


@router.post("/create/customer")
def create_customer_router(body: CustomerCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new customer.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "email": body.email,
            "phone": body.phone,
        },
        workflow_type="customer_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create customer")
        )
    
    return result.get("data")


@router.get("/get/customers")
def get_customers_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all customers.
    """
    customers = CustomerService().read_all()
    return [customer.to_dict() for customer in customers]


@router.get("/get/customer/{public_id}")
def get_customer_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a customer by public ID.
    """
    customer = CustomerService().read_by_public_id(public_id=public_id)
    return customer.to_dict()


@router.put("/update/customer/{public_id}")
def update_customer_by_public_id_router(public_id: str, body: CustomerUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a customer by public ID.
    
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
            "name": body.name,
            "email": body.email,
            "phone": body.phone,
        },
        workflow_type="customer_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update customer")
        )
    
    return result.get("data")


@router.delete("/delete/customer/{public_id}")
def delete_customer_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a customer by public ID.
    
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
        workflow_type="customer_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete customer")
        )
    
    return result.get("data")
