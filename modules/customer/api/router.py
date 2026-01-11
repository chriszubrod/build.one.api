# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.customer.api.schemas import CustomerCreate, CustomerUpdate
from modules.customer.business.service import CustomerService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "customer"])


@router.post("/create/customer")
def create_customer_router(body: CustomerCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new customer.
    """
    customer = CustomerService().create(
        name=body.name,
        email=body.email,
        phone=body.phone,
    )
    return customer.to_dict()


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
    """
    customer = CustomerService().update_by_public_id(public_id=public_id, customer=body)
    return customer.to_dict()


@router.delete("/delete/customer/{public_id}")
def delete_customer_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a customer by public ID.
    """
    customer = CustomerService().delete_by_public_id(public_id=public_id)
    return customer.to_dict()
