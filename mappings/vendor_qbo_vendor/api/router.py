# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from mappings.vendor_qbo_vendor.api.schemas import MapVendorQboVendorCreate, MapVendorQboVendorUpdate
from mappings.vendor_qbo_vendor.business.service import MapVendorQboVendorService
from modules.auth.business.service import get_current_user_api as get_current_vendor_qbo_vendor_api

router = APIRouter(prefix="/api/v1", tags=["api", "vendor-qbo-vendor"])
service = MapVendorQboVendorService()


@router.post("/create/vendor-qbo-vendor")
def create_vendor_qbo_vendor_router(body: MapVendorQboVendorCreate, current_user: dict = Depends(get_current_vendor_qbo_vendor_api)):
    """
    Create a new vendor qbo vendor mapping record.
    """
    vendor_qbo_vendor = service.create(
        vendor_id=body.vendor_id,
        qbo_vendor_id=body.qbo_vendor_id,
    )
    return vendor_qbo_vendor.to_dict()


@router.get("/get/vendor-qbo-vendors")
def get_vendor_qbo_vendors_router(current_user: dict = Depends(get_current_vendor_qbo_vendor_api)):
    """
    Read all vendor qbo vendor mapping records.
    """
    vendor_qbo_vendors = service.read_all()
    return [vendor_qbo_vendor.to_dict() for vendor_qbo_vendor in vendor_qbo_vendors]


@router.get("/get/vendor-qbo-vendor/{public_id}")
def get_vendor_qbo_vendor_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_qbo_vendor_api)):
    """
    Read a vendor qbo vendor mapping record by public ID.
    """
    vendor_qbo_vendor = service.read_by_public_id(public_id=public_id)
    return vendor_qbo_vendor.to_dict()


@router.put("/update/vendor-qbo-vendor/{public_id}")
def update_vendor_qbo_vendor_by_public_id_router(public_id: str, body: MapVendorQboVendorUpdate, current_user: dict = Depends(get_current_vendor_qbo_vendor_api)):
    """
    Update a vendor qbo vendor mapping record by public ID.
    """
    vendor_qbo_vendor = service.update_by_public_id(public_id=public_id, vendor_id=body.vendor_id, qbo_vendor_id=body.qbo_vendor_id)
    return vendor_qbo_vendor.to_dict()


@router.delete("/delete/vendor-qbo-vendor/{public_id}")
def delete_vendor_qbo_vendor_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_qbo_vendor_api)):
    """
    Delete a vendor qbo vendor mapping record by public ID.
    """
    vendor_qbo_vendor = service.delete_by_public_id(public_id=public_id)
    return vendor_qbo_vendor.to_dict()
