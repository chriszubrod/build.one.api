# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.role.api.schemas import RoleCreate, RoleUpdate
from entities.role.business.service import RoleService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "role"])


@router.post("/create/role")
def create_role_router(body: RoleCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new role.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
        },
        workflow_type="role_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create role")
        )

    return result.get("data")


@router.get("/get/roles")
def get_roles_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all roles.
    """
    roles = RoleService().read_all()
    return [role.to_dict() for role in roles]


@router.get("/get/role/{public_id}")
def get_role_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a role by public ID.
    """
    role = RoleService().read_by_public_id(public_id=public_id)
    return role.to_dict()


@router.put("/update/role/{public_id}")
def update_role_by_public_id_router(public_id: str, body: RoleUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a role by public ID.

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
        },
        workflow_type="role_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update role")
        )

    return result.get("data")


@router.delete("/delete/role/{public_id}")
def delete_role_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a role by public ID.

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
        workflow_type="role_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete role")
        )

    return result.get("data")
