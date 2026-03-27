# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.role_module.api.schemas import RoleModuleCreate, RoleModuleUpdate
from entities.role_module.business.service import RoleModuleService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "role_module"])


@router.post("/create/role_module")
def create_role_module_router(body: RoleModuleCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new role module.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "role_id": body.role_id,
            "module_id": body.module_id,
            "can_create": body.can_create,
            "can_read": body.can_read,
            "can_update": body.can_update,
            "can_delete": body.can_delete,
            "can_submit": body.can_submit,
            "can_approve": body.can_approve,
            "can_complete": body.can_complete,
        },
        workflow_type="role_module_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create role module")
        )
    
    return result.get("data")


@router.get("/get/role_modules")
def get_role_modules_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all role modules.
    """
    role_modules = RoleModuleService().read_all()
    return [role_module.to_dict() for role_module in role_modules]


@router.get("/get/role_module/{public_id}")
def get_role_module_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a role module by public ID.
    """
    role_module = RoleModuleService().read_by_public_id(public_id=public_id)
    return role_module.to_dict()


@router.put("/update/role_module/{public_id}")
def update_role_module_by_public_id_router(public_id: str, body: RoleModuleUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a role module by public ID.
    
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
            "role_id": body.role_id,
            "module_id": body.module_id,
            "can_create": body.can_create,
            "can_read": body.can_read,
            "can_update": body.can_update,
            "can_delete": body.can_delete,
            "can_submit": body.can_submit,
            "can_approve": body.can_approve,
            "can_complete": body.can_complete,
        },
        workflow_type="role_module_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update role module")
        )
    
    return result.get("data")


@router.delete("/delete/role_module/{public_id}")
def delete_role_module_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a role module by public ID.
    
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
        workflow_type="role_module_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete role module")
        )
    
    return result.get("data")
