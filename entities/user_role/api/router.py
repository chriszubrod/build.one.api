# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.user_role.api.schemas import UserRoleCreate, UserRoleUpdate
from entities.user_role.business.service import UserRoleService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "user_role"])


@router.post("/create/user_role")
def create_user_role_router(body: UserRoleCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new user role.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "user_id": body.user_id,
            "role_id": body.role_id,
        },
        workflow_type="user_role_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create user role")
        )
    
    return result.get("data")


@router.get("/get/user_roles")
def get_user_roles_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all user roles.
    """
    user_roles = UserRoleService().read_all()
    return [user_role.to_dict() for user_role in user_roles]


@router.get("/get/user_role/{public_id}")
def get_user_role_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a user role by public ID.
    """
    user_role = UserRoleService().read_by_public_id(public_id=public_id)
    return user_role.to_dict()


@router.put("/update/user_role/{public_id}")
def update_user_role_by_public_id_router(public_id: str, body: UserRoleUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a user role by public ID.
    
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
            "user_id": body.user_id,
            "role_id": body.role_id,
        },
        workflow_type="user_role_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update user role")
        )
    
    return result.get("data")


@router.delete("/delete/user_role/{public_id}")
def delete_user_role_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a user role by public ID.
    
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
        workflow_type="user_role_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete user role")
        )
    
    return result.get("data")
