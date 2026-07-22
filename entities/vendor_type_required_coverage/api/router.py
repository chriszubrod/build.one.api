# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from entities.vendor_type_required_coverage.api.schemas import VendorTypeRequiredCoverageCreate
from entities.vendor_type_required_coverage.business.service import VendorTypeRequiredCoverageService
from shared.api.responses import item_response, list_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["vendor-type-required-coverage"])
service = VendorTypeRequiredCoverageService()


@router.post("/create/vendor-type-required-coverage")
def create_vendor_type_required_coverage_router(
    body: VendorTypeRequiredCoverageCreate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    """
    Create a required coverage rule for a vendor type.
    """
    try:
        row = service.create(
            vendor_type_id=body.vendor_type_id,
            coverage_type=body.coverage_type,
        )
        return item_response(row.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/vendor-type-required-coverages")
def get_vendor_type_required_coverages_router(
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    """
    Read all vendor type required coverage rows.
    """
    try:
        rows = service.read_all()
        return list_response([r.to_dict() for r in rows])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/vendor-type-required-coverages/by-vendor-type/{vendor_type_id}")
def get_vendor_type_required_coverages_by_vendor_type_id_router(
    vendor_type_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    """
    Read required coverages for a vendor type.
    """
    try:
        rows = service.read_by_vendor_type_id(vendor_type_id=int(vendor_type_id))
        return list_response([r.to_dict() for r in rows])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/vendor-type-required-coverage/{public_id}")
def delete_vendor_type_required_coverage_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete")),
):
    """
    Delete a vendor type required coverage row by public ID.
    """
    try:
        deleted = service.delete_by_public_id(public_id=public_id)
        if not deleted:
            raise_not_found("Vendor type required coverage")
        return item_response({"public_id": public_id, "deleted": True})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
