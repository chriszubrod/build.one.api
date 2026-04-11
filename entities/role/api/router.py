# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.role.api.schemas import RoleCreate, RoleUpdate
from entities.role.business.service import RoleService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "role"])


@router.post("/create/role")
def create_role_router(body: RoleCreate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_create"))):
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
        raise_workflow_error(result.get("error", ""), "Failed to create role")

    return item_response(result.get("data"))


@router.get("/get/roles")
def get_roles_router(current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read all roles.
    """
    roles = RoleService().read_all()
    return list_response([role.to_dict() for role in roles])


@router.get("/get/role/{public_id}")
def get_role_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read a role by public ID.
    """
    role = RoleService().read_by_public_id(public_id=public_id)
    return item_response(role.to_dict())


@router.put("/update/role/{public_id}")
def update_role_by_public_id_router(public_id: str, body: RoleUpdate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_update"))):
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
        raise_workflow_error(result.get("error", ""), "Failed to update role")

    return item_response(result.get("data"))


@router.delete("/delete/role/{public_id}")
def delete_role_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_delete"))):
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
        raise_workflow_error(result.get("error", ""), "Failed to delete role")

    return item_response(result.get("data"))
