# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, HTTPException, Depends, status

# Local Imports
from services.cost_code.business.service import CostCodeService
from services.cost_code.api.schemas import (
    CostCodeCreate,
    CostCodeUpdate,
)
from services.auth.business.service import get_current_user_api
from workflows.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "cost-code"])
service = CostCodeService()


@router.post("/create/cost-code")
def create_cost_code_router(body: CostCodeCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new cost code.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "number": body.number,
            "name": body.name,
            "description": body.description,
        },
        workflow_type="cost_code_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create cost code")
        )
    
    return result.get("data")


@router.get("/get/cost-codes")
def get_cost_codes_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all cost codes.
    """
    cost_codes = service.read_all()
    return [cost_code.to_dict() for cost_code in cost_codes]


@router.get("/get/cost-code/{public_id}")
def get_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a cost code by public ID.
    """
    cost_code = service.read_by_public_id(public_id=public_id)
    if not cost_code:
        raise HTTPException(status_code=404, detail="Cost code not found.")
    return cost_code.to_dict()


@router.put("/update/cost-code/{public_id}")
def update_cost_code_by_id_router(public_id: str, body: CostCodeUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a cost code by ID.
    
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
            "number": body.number,
            "name": body.name,
            "description": body.description,
        },
        workflow_type="cost_code_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update cost code")
        )
    
    return result.get("data")


@router.delete("/delete/cost-code/{public_id}")
def delete_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Soft delete a cost code by ID.
    
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
        workflow_type="cost_code_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete cost code")
        )
    
    return result.get("data")
