# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.module.api.schemas import ModuleCreate, ModuleUpdate
from entities.module.business.service import ModuleService
from entities.auth.business.service import get_current_user_api
from workflows.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "module"])


@router.post("/create/module")
def create_module_router(body: ModuleCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new module.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "route": body.route,
        },
        workflow_type="module_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create module")
        )
    
    return result.get("data")


@router.get("/get/modules")
def get_modules_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all modules.
    """
    modules = ModuleService().read_all()
    return [module.to_dict() for module in modules]


@router.get("/get/module/{public_id}")
def get_module_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a module by public ID.
    """
    module = ModuleService().read_by_public_id(public_id=public_id)
    return module.to_dict()


@router.put("/update/module/{public_id}")
def update_module_by_public_id_router(public_id: str, body: ModuleUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a module by public ID.
    
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
            "route": body.route,
        },
        workflow_type="module_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update module")
        )
    
    return result.get("data")


@router.delete("/delete/module/{public_id}")
def delete_module_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a module by public ID.
    
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
        workflow_type="module_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete module")
        )
    
    return result.get("data")
