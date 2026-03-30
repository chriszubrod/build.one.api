# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, HTTPException, Depends, status

# Local Imports
from entities.cost_code.business.service import CostCodeService
from entities.cost_code.api.schemas import (
    CostCodeCreate,
    CostCodeUpdate,
)
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "cost-code"])
service = CostCodeService()


@router.post("/create/cost-code")
def create_cost_code_router(body: CostCodeCreate, current_user: dict = Depends(require_module_api(Modules.COST_CODES, "can_create"))):
    """
    Create a new cost code.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "number": body.number,
            "name": body.name,
            "description": body.description,
        },
        workflow_type="cost_code_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create cost code")
        )
    
    return result.get("data")


@router.get("/get/cost-codes")
def get_cost_codes_router(current_user: dict = Depends(require_module_api(Modules.COST_CODES))):
    """
    Read all cost codes.
    """
    cost_codes = service.read_all()
    return [cost_code.to_dict() for cost_code in cost_codes]


@router.get("/get/cost-code/{public_id}")
def get_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.COST_CODES))):
    """
    Read a cost code by public ID.
    """
    cost_code = service.read_by_public_id(public_id=public_id)
    if not cost_code:
        raise HTTPException(status_code=404, detail="Cost code not found.")
    return cost_code.to_dict()


@router.put("/update/cost-code/{public_id}")
def update_cost_code_by_id_router(public_id: str, body: CostCodeUpdate, current_user: dict = Depends(require_module_api(Modules.COST_CODES, "can_update"))):
    """
    Update a cost code by ID.
    
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
            "number": body.number,
            "name": body.name,
            "description": body.description,
        },
        workflow_type="cost_code_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update cost code")
        )
    
    return result.get("data")


@router.delete("/delete/cost-code/{public_id}")
def delete_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.COST_CODES, "can_delete"))):
    """
    Soft delete a cost code by ID.
    
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
        workflow_type="cost_code_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete cost code")
        )
    
    return result.get("data")
