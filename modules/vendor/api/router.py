# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

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
    try:
        vendor = service.create(
            name=body.name,
            abbreviation=body.abbreviation,
            taxpayer_public_id=body.taxpayer_public_id,
            vendor_type_public_id=body.vendor_type_public_id,
            is_draft=body.is_draft,
        )
        return vendor.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/vendors")
def get_vendors_router(current_user: dict = Depends(get_current_vendor_api)):
    """
    Read all vendors.
    """
    try:
        vendors = service.read_all()
        return [vendor.to_dict() for vendor in vendors]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/vendor/{public_id}")
def get_vendor_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_api)):
    """
    Read a vendor by public ID.
    """
    try:
        vendor = service.read_by_public_id(public_id=public_id)
        return vendor.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/vendor/{public_id}")
def update_vendor_by_public_id_router(public_id: str, body: VendorUpdate, current_user: dict = Depends(get_current_vendor_api)):
    """
    Update a vendor by public ID.
    """
    try:
        vendor = service.update_by_public_id(public_id=public_id, vendor=body)
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")
        return vendor.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/vendor/{public_id}")
def delete_vendor_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_api)):
    """
    Delete a vendor by public ID.
    """
    try:
        vendor = service.delete_by_public_id(public_id=public_id)
        return vendor.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
