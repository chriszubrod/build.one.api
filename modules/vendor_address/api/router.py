# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.vendor_address.api.schemas import VendorAddressCreate, VendorAddressUpdate
from modules.vendor_address.business.service import VendorAddressService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "vendor_address"])


@router.post("/create/vendor_address")
def create_vendor_address_router(body: VendorAddressCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new vendor address.
    """
    vendor_address = VendorAddressService().create(
        vendor_id=body.vendor_id,
        address_id=body.address_id,
        address_type_id=body.address_type_id,
    )
    return vendor_address.to_dict()


@router.get("/get/vendor_addresses")
def get_vendor_addresses_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all vendor addresses.
    """
    vendor_addresses = VendorAddressService().read_all()
    return [vendor_address.to_dict() for vendor_address in vendor_addresses]


@router.get("/get/vendor_address/{public_id}")
def get_vendor_address_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a vendor address by public ID.
    """
    vendor_address = VendorAddressService().read_by_public_id(public_id=public_id)
    return vendor_address.to_dict()


@router.get("/get/vendor_address/vendor/{vendor_id}")
def get_vendor_address_by_vendor_id_router(vendor_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a vendor address by vendor ID.
    """
    vendor_address = VendorAddressService().read_by_vendor_id(vendor_id=vendor_id)
    return vendor_address.to_dict()


@router.get("/get/vendor_address/address/{address_id}")
def get_vendor_address_by_address_id_router(address_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a vendor address by address ID.
    """
    vendor_address = VendorAddressService().read_by_address_id(address_id=address_id)
    return vendor_address.to_dict()


@router.get("/get/vendor_address/address_type/{address_type_id}")
def get_vendor_address_by_address_type_id_router(address_type_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a vendor address by address type ID.
    """
    vendor_address = VendorAddressService().read_by_address_type_id(address_type_id=address_type_id)
    return vendor_address.to_dict()


@router.put("/update/vendor_address/{public_id}")
def update_vendor_address_by_public_id_router(public_id: str, body: VendorAddressUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a vendor address by public ID.
    """
    vendor_address = VendorAddressService().update_by_public_id(public_id=public_id, vendor_address=body)
    return vendor_address.to_dict()


@router.delete("/delete/vendor_address/{public_id}")
def delete_vendor_address_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a vendor address by public ID.
    """
    vendor_address = VendorAddressService().delete_by_public_id(public_id=public_id)
    return vendor_address.to_dict()
