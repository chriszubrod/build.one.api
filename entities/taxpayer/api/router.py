# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.taxpayer.api.schemas import TaxpayerCreate, TaxpayerUpdate
from entities.taxpayer.business.service import TaxpayerService
from entities.auth.business.service import get_current_user_api as get_current_taxpayer_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "taxpayer"])
service = TaxpayerService()


@router.post("/create/taxpayer")
def create_taxpayer_router(body: TaxpayerCreate, current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Create a new taxpayer.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "entity_name": body.entity_name,
            "business_name": body.business_name,
            "classification": body.classification,
            "taxpayer_id_number": body.taxpayer_id_number,
        },
        workflow_type="taxpayer_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create taxpayer")
        )
    
    return result.get("data")


@router.get("/get/taxpayers")
def get_taxpayers_router(current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Read all taxpayers.
    """
    taxpayers = service.read_all()
    return [taxpayer.to_dict() for taxpayer in taxpayers]


@router.get("/get/taxpayer/{public_id}")
def get_taxpayer_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Read a taxpayer by public ID.
    """
    taxpayer = service.read_by_public_id(public_id=public_id)
    return taxpayer.to_dict()


@router.put("/update/taxpayer/{public_id}")
def update_taxpayer_by_public_id_router(public_id: str, body: TaxpayerUpdate, current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Update a taxpayer by public ID.
    
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
            "entity_name": body.entity_name,
            "business_name": body.business_name,
            "classification": body.classification.value if body.classification else None,
            "taxpayer_id_number": body.taxpayer_id_number,
        },
        workflow_type="taxpayer_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update taxpayer")
        )
    
    return result.get("data")


@router.delete("/delete/taxpayer/{public_id}")
def delete_taxpayer_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_taxpayer_api)):
    """
    Delete a taxpayer by public ID.
    
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
        workflow_type="taxpayer_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete taxpayer")
        )
    
    return result.get("data")
