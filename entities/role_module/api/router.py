# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.role_module.api.schemas import RoleModuleCreate, RoleModuleUpdate
from entities.role_module.business.service import RoleModuleService
from entities.user_role.business.service import UserRoleService
from shared.profile_events import publish_profile_changed_many
from shared.rbac import invalidate_all_caches, require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error


def _user_ids_for_role_ids(role_ids) -> set[int]:
    """Return user_ids currently assigned any of the given role_ids."""
    if not role_ids:
        return set()
    unique_role_ids = {rid for rid in role_ids if rid is not None}
    if not unique_role_ids:
        return set()
    all_user_roles = UserRoleService().read_all()
    return {ur.user_id for ur in all_user_roles if ur.role_id in unique_role_ids and ur.user_id is not None}

router = APIRouter(prefix="/api/v1", tags=["api", "role_module"])


@router.post("/create/role_module")
def create_role_module_router(body: RoleModuleCreate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_create"))):
    """
    Create a new role module.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
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
    
    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create role module")

    invalidate_all_caches()
    publish_profile_changed_many(_user_ids_for_role_ids([body.role_id]))
    return item_response(result.get("data"))


@router.get("/get/role_modules")
def get_role_modules_router(current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read all role modules.
    """
    role_modules = RoleModuleService().read_all()
    return list_response([role_module.to_dict() for role_module in role_modules])


@router.get("/get/role_module/{public_id}")
def get_role_module_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read a role module by public ID.
    """
    role_module = RoleModuleService().read_by_public_id(public_id=public_id)
    return item_response(role_module.to_dict())


@router.put("/update/role_module/{public_id}")
def update_role_module_by_public_id_router(public_id: str, body: RoleModuleUpdate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_update"))):
    """
    Update a role module by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    existing = RoleModuleService().read_by_public_id(public_id=public_id)
    affected_role_ids: list = []
    if existing and existing.role_id is not None:
        affected_role_ids.append(existing.role_id)
    if body.role_id is not None:
        affected_role_ids.append(body.role_id)

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
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

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update role module")

    invalidate_all_caches()
    publish_profile_changed_many(_user_ids_for_role_ids(affected_role_ids))
    return item_response(result.get("data"))


@router.delete("/delete/role_module/{public_id}")
def delete_role_module_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_delete"))):
    """
    Delete a role module by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    existing = RoleModuleService().read_by_public_id(public_id=public_id)
    affected_role_id = existing.role_id if existing else None

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="role_module_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete role module")

    invalidate_all_caches()
    if affected_role_id is not None:
        publish_profile_changed_many(_user_ids_for_role_ids([affected_role_id]))
    return item_response(result.get("data"))
