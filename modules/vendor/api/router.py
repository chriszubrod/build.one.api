# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.vendor.api.schemas import VendorCreate, VendorUpdate
from modules.vendor.business.service import VendorService
from modules.auth.business.service import get_current_user_api as get_current_vendor_api

router = APIRouter(prefix="/api/v1", tags=["api", "vendor"])
service = VendorService()


@router.post("/create/vendor")
def create_vendor_router(body: VendorCreate, current_user: dict = Depends(get_current_vendor_api)):
    """
    Create a new vendor.
    """
    vendor = service.create(
        name=body.name,
        abbreviation=body.abbreviation,
    )
    return vendor.to_dict()


@router.get("/get/vendors")
def get_vendors_router(current_user: dict = Depends(get_current_vendor_api)):
    """
    Read all vendors.
    """
    vendors = service.read_all()
    return [vendor.to_dict() for vendor in vendors]


@router.get("/get/vendor/{public_id}")
def get_vendor_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_api)):
    """
    Read a vendor by public ID.
    """
    vendor = service.read_by_public_id(public_id=public_id)
    return vendor.to_dict()


@router.put("/update/vendor/{public_id}")
def update_vendor_by_public_id_router(public_id: str, body: VendorUpdate, current_user: dict = Depends(get_current_vendor_api)):
    """
    Update a vendor by public ID.
    """
    vendor = service.update_by_public_id(public_id=public_id, vendor=body)
    return vendor.to_dict()


@router.delete("/delete/vendor/{public_id}")
def delete_vendor_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_api)):
    """
    Delete a vendor by public ID.
    """
    vendor = service.delete_by_public_id(public_id=public_id)
    return vendor.to_dict()
