# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from entities.address.api.schemas import AddressCreate, AddressUpdate
from entities.address.business.service import AddressService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "address"])


@router.post("/create/address")
def create_address_router(body: AddressCreate, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create"))):
    """
    Create a new address.
    """
    address = AddressService().create(
        street_one=body.street_one,
        street_two=body.street_two,
        city=body.city,
        state=body.state,
        zip=body.zip,
    )
    return address.to_dict()


@router.get("/get/addresses")
def get_addresses_router(current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read all addresses.
    """
    addresses = AddressService().read_all()
    return [address.to_dict() for address in addresses]


@router.get("/get/address/{public_id}")
def get_address_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read an address by public ID.
    """
    address = AddressService().read_by_public_id(public_id=public_id)
    return address.to_dict()


@router.put("/update/address/{public_id}")
def update_address_by_public_id_router(public_id: str, body: AddressUpdate, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update"))):
    """
    Update an address by public ID.
    """
    try:
        address = AddressService().update_by_public_id(public_id=public_id, address=body)
        if not address:
            raise HTTPException(status_code=404, detail="Address not found")
        return address.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/address/{public_id}")
def delete_address_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete"))):
    """
    Delete an address by public ID.
    """
    address = AddressService().delete_by_public_id(public_id=public_id)
    return address.to_dict()
