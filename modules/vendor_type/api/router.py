# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.vendor_type.api.schemas import VendorTypeCreate, VendorTypeUpdate
from modules.vendor_type.business.service import VendorTypeService
from modules.auth.business.service import get_current_user_api as get_current_vendor_type_api

router = APIRouter(prefix="/api/v1", tags=["api", "vendor-type"])
service = VendorTypeService()


@router.post("/create/vendor-type")
def create_vendor_type_router(body: VendorTypeCreate, current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Create a new vendor type.
    """
    vendor_type = service.create(
        name=body.name,
        description=body.description,
    )
    return vendor_type.to_dict()


@router.get("/get/vendor-types")
def get_vendor_types_router(current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Read all vendor types.
    """
    vendor_types = service.read_all()
    return [vendor_type.to_dict() for vendor_type in vendor_types]


@router.get("/get/vendor-type/{public_id}")
def get_vendor_type_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Read a vendor type by public ID.
    """
    vendor_type = service.read_by_public_id(public_id=public_id)
    return vendor_type.to_dict()


@router.put("/update/vendor-type/{public_id}")
def update_vendor_type_by_public_id_router(public_id: str, body: VendorTypeUpdate, current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Update a vendor type by public ID.
    """
    vendor_type = service.update_by_public_id(public_id=public_id, vendor_type=body)
    return vendor_type.to_dict()


@router.delete("/delete/vendor-type/{public_id}")
def delete_vendor_type_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Delete a vendor type by public ID.
    """
    vendor_type = service.delete_by_public_id(public_id=public_id)
    return vendor_type.to_dict()
