# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.taxpayer.api.schemas import TaxpayerCreate, TaxpayerUpdate
from entities.taxpayer.business.service import TaxpayerService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "taxpayer"])
service = TaxpayerService()


@router.post("/create/taxpayer")
def create_taxpayer_router(body: TaxpayerCreate, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create"))):
    """
    Create a new taxpayer.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "entity_name": body.entity_name,
            "business_name": body.business_name,
            "classification": body.classification,
            "taxpayer_id_number": body.taxpayer_id_number,
            "is_signed": body.is_signed,
            "signature_date": body.signature_date,
        },
        workflow_type="taxpayer_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create taxpayer")
    
    return item_response(result.get("data"))


@router.get("/get/taxpayers")
def get_taxpayers_router(current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read all taxpayers.
    """
    taxpayers = service.read_all()
    return list_response([taxpayer.to_dict() for taxpayer in taxpayers])


@router.get("/get/taxpayer/{public_id}")
def get_taxpayer_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read a taxpayer by public ID.
    """
    taxpayer = service.read_by_public_id(public_id=public_id)
    return item_response(taxpayer.to_dict())


@router.put("/update/taxpayer/{public_id}")
def update_taxpayer_by_public_id_router(public_id: str, body: TaxpayerUpdate, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update"))):
    """
    Update a taxpayer by public ID.
    
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
            "entity_name": body.entity_name,
            "business_name": body.business_name,
            "classification": body.classification.value if body.classification else None,
            "taxpayer_id_number": body.taxpayer_id_number,
            "is_signed": body.is_signed,
            "signature_date": body.signature_date,
        },
        workflow_type="taxpayer_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update taxpayer")
    
    return item_response(result.get("data"))


@router.delete("/delete/taxpayer/{public_id}")
def delete_taxpayer_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete"))):
    """
    Delete a taxpayer by public ID.
    
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
        workflow_type="taxpayer_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete taxpayer")
    
    return item_response(result.get("data"))
