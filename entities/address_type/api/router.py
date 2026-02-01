# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.address_type.api.schemas import AddressTypeCreate, AddressTypeUpdate
from entities.address_type.business.service import AddressTypeService
from entities.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "address_type"])


@router.post("/create/address_type")
def create_address_type_router(body: AddressTypeCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new address type.
    """
    address_type = AddressTypeService().create(
        name=body.name,
        description=body.description,
        display_order=body.display_order,
    )
    return address_type.to_dict()


@router.get("/get/address_types")
def get_address_types_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all address types.
    """
    address_types = AddressTypeService().read_all()
    return [address_type.to_dict() for address_type in address_types]


@router.get("/get/address_type/{public_id}")
def get_address_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read an address type by public ID.
    """
    address_type = AddressTypeService().read_by_public_id(public_id=public_id)
    return address_type.to_dict()


@router.put("/update/address_type/{public_id}")
def update_address_type_by_public_id_router(public_id: str, body: AddressTypeUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update an address type by public ID.
    """
    address_type = AddressTypeService().update_by_public_id(public_id=public_id, address_type=body)
    return address_type.to_dict()


@router.delete("/delete/address_type/{public_id}")
def delete_address_type_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete an address type by public ID.
    """
    address_type = AddressTypeService().delete_by_public_id(public_id=public_id)
    return address_type.to_dict()
