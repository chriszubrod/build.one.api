# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.user_role.api.schemas import UserRoleCreate, UserRoleUpdate
from entities.user_role.business.service import UserRoleService
from shared.profile_events import publish_profile_changed
from shared.rbac import invalidate_all_caches, require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "user_role"])


@router.post("/create/user_role")
def create_user_role_router(body: UserRoleCreate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_create"))):
    """
    Create a new user role.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "user_id": body.user_id,
            "role_id": body.role_id,
        },
        workflow_type="user_role_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create user role")

    invalidate_all_caches()
    publish_profile_changed(body.user_id)
    return item_response(result.get("data"))


@router.get("/get/user_roles")
def get_user_roles_router(current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read all user roles.
    """
    user_roles = UserRoleService().read_all()
    return list_response([user_role.to_dict() for user_role in user_roles])


@router.get("/get/user_role/{public_id}")
def get_user_role_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read a user role by public ID.
    """
    user_role = UserRoleService().read_by_public_id(public_id=public_id)
    return item_response(user_role.to_dict())


@router.put("/update/user_role/{public_id}")
def update_user_role_by_public_id_router(public_id: str, body: UserRoleUpdate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_update"))):
    """
    Update a user role by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    existing = UserRoleService().read_by_public_id(public_id=public_id)
    affected_user_ids: set[int] = set()
    if existing and existing.user_id is not None:
        affected_user_ids.add(existing.user_id)
    if body.user_id is not None:
        affected_user_ids.add(body.user_id)

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
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

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update user role")

    invalidate_all_caches()
    for uid in affected_user_ids:
        publish_profile_changed(uid)
    return item_response(result.get("data"))


@router.delete("/delete/user_role/{public_id}")
def delete_user_role_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_delete"))):
    """
    Delete a user role by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    existing = UserRoleService().read_by_public_id(public_id=public_id)
    affected_user_id = existing.user_id if existing else None

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="user_role_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete user role")

    invalidate_all_caches()
    if affected_user_id is not None:
        publish_profile_changed(affected_user_id)
    return item_response(result.get("data"))
