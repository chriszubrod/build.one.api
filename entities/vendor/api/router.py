# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from services.vendor.api.schemas import VendorCreate, VendorUpdate
from services.vendor.business.service import VendorService
from services.auth.business.service import get_current_user_api as get_current_vendor_api
from workflows.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "vendor"])
service = VendorService()


@router.post("/create/vendor")
def create_vendor_router(body: VendorCreate, current_user: dict = Depends(get_current_vendor_api)):
    """
    Create a new vendor.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "abbreviation": body.abbreviation,
            "taxpayer_public_id": body.taxpayer_public_id,
            "vendor_type_public_id": body.vendor_type_public_id,
            "is_draft": body.is_draft,
        },
        workflow_type="vendor_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create vendor")
        )
    
    return result.get("data")


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
            "abbreviation": body.abbreviation,
            "taxpayer_public_id": body.taxpayer_public_id,
            "vendor_type_public_id": body.vendor_type_public_id,
            "is_draft": body.is_draft,
        },
        workflow_type="vendor_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update vendor")
        )
    
    return result.get("data")


@router.delete("/delete/vendor/{public_id}")
def delete_vendor_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_api)):
    """
    Delete a vendor by public ID.
    
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
        workflow_type="vendor_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete vendor")
        )
    
    return result.get("data")
