# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.vendor_type.api.schemas import VendorTypeCreate, VendorTypeUpdate
from entities.vendor_type.business.service import VendorTypeService
from entities.auth.business.service import get_current_user_api as get_current_vendor_type_api
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "vendor-type"])
service = VendorTypeService()


@router.post("/create/vendor-type")
def create_vendor_type_router(body: VendorTypeCreate, current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Create a new vendor type.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "description": body.description,
        },
        workflow_type="vendor_type_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create vendor type")
    
    return item_response(result.get("data"))


@router.get("/get/vendor-types")
def get_vendor_types_router(current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Read all vendor types.
    """
    vendor_types = service.read_all()
    return list_response([vendor_type.to_dict() for vendor_type in vendor_types])


@router.get("/get/vendor-type/{public_id}")
def get_vendor_type_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Read a vendor type by public ID.
    """
    vendor_type = service.read_by_public_id(public_id=public_id)
    return item_response(vendor_type.to_dict())


@router.put("/update/vendor-type/{public_id}")
def update_vendor_type_by_public_id_router(public_id: str, body: VendorTypeUpdate, current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Update a vendor type by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "name": body.name,
            "description": body.description,
        },
        workflow_type="vendor_type_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update vendor type")
    
    return item_response(result.get("data"))


@router.delete("/delete/vendor-type/{public_id}")
def delete_vendor_type_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_vendor_type_api)):
    """
    Delete a vendor type by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="vendor_type_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete vendor type")
    
    return item_response(result.get("data"))
