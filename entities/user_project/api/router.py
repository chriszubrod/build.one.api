# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.role.business.service import RoleService
from entities.user_project.api.schemas import UserProjectCreate, UserProjectUpdate
from entities.user_project.business.service import UserProjectService
from shared.profile_events import publish_profile_changed
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "user_project"])


def _resolve_role_id(role_public_id: Optional[str]) -> Optional[int]:
    if role_public_id is None:
        return None
    role = RoleService().read_by_public_id(public_id=role_public_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role with public_id {role_public_id} not found.",
        )
    return role.id


@router.post("/create/user_project")
def create_user_project_router(body: UserProjectCreate, current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_create"))):
    """
    Create a new user project.

    Routes through the workflow engine for audit logging and state tracking.
    """
    role_id = _resolve_role_id(body.role_public_id)

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "user_id": body.user_id,
            "project_id": body.project_id,
            "role_id": role_id,
        },
        workflow_type="user_project_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create user project")

    publish_profile_changed(body.user_id)
    return item_response(result.get("data"))


@router.get("/get/user_projects")
def get_user_projects_router(current_user: dict = Depends(require_module_api(Modules.PROJECTS))):
    """
    Read all user projects.
    """
    user_projects = UserProjectService().read_all()
    return list_response([user_project.to_dict() for user_project in user_projects])


@router.get("/get/user_projects/user/{user_id}")
def get_user_projects_by_user_id_router(user_id: int, current_user: dict = Depends(require_module_api(Modules.PROJECTS))):
    """
    Read all user projects by user ID.
    """
    user_projects = UserProjectService().read_by_user_id(user_id=user_id)
    return list_response([user_project.to_dict() for user_project in user_projects])


@router.get("/get/user_project/{public_id}")
def get_user_project_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.PROJECTS))):
    """
    Read a user project by public ID.
    """
    user_project = UserProjectService().read_by_public_id(public_id=public_id)
    return item_response(user_project.to_dict())


@router.put("/update/user_project/{public_id}")
def update_user_project_by_public_id_router(public_id: str, body: UserProjectUpdate, current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_update"))):
    """
    Update a user project by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    existing = UserProjectService().read_by_public_id(public_id=public_id)
    affected_user_ids: set[int] = set()
    if existing and existing.user_id is not None:
        affected_user_ids.add(existing.user_id)
    if body.user_id is not None:
        affected_user_ids.add(body.user_id)

    role_id = _resolve_role_id(body.role_public_id)

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "user_id": body.user_id,
            "project_id": body.project_id,
            "role_id": role_id,
        },
        workflow_type="user_project_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update user project")

    for uid in affected_user_ids:
        publish_profile_changed(uid)
    return item_response(result.get("data"))


@router.delete("/delete/user_project/{public_id}")
def delete_user_project_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_delete"))):
    """
    Delete a user project by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    existing = UserProjectService().read_by_public_id(public_id=public_id)
    affected_user_id = existing.user_id if existing else None

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="user_project_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete user project")

    if affected_user_id is not None:
        publish_profile_changed(affected_user_id)
    return item_response(result.get("data"))
