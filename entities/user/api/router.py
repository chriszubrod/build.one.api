# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.auth.business.service import get_current_user_api
from entities.user.api.schemas import UserCreate, UserUpdate, UserWorkerLinkUpdate
from entities.user.business.service import UserService
from shared.authz import current_user_id
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_not_found, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "user"])


@router.post("/create/user")
def create_user_router(body: UserCreate, current_user: dict = Depends(require_module_api(Modules.USERS, "can_create"))):
    """
    Create a new user.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "firstname": body.firstname,
            "lastname": body.lastname,
        },
        workflow_type="user_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create user")
    
    return item_response(result.get("data"))


@router.get("/get/users")
def get_users_router(
    include_agents: bool = False,
    current_user: dict = Depends(require_module_api(Modules.USERS)),
):
    """
    Read users. Agent users are hidden by default — pass
    `?include_agents=true` to surface them.
    """
    users = UserService().read_all(include_agents=include_agents)
    return list_response([user.to_dict() for user in users])


@router.get("/get/user/me")
def get_my_user_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read the caller's own User row.

    Auth-only (no module RBAC) — the iOS bootstrap must resolve the
    signed-in user's profile without a USERS module grant, which field
    workers don't hold (2026-06-09 onboarding lockout: the /get/users
    fallback 403'd for any role lacking Users-read). Declared before
    /get/user/{public_id} so "me" doesn't match as a public_id.
    """
    user_id = current_user_id.get()
    if user_id is None:
        raise_not_found("User", "me")
    user = UserService().read_by_id(id=user_id)
    if not user:
        raise_not_found("User", "me")
    return item_response(user.to_dict())


@router.get("/get/user/{public_id}")
def get_user_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.USERS))):
    """
    Read a user by public ID.
    """
    user = UserService().read_by_public_id(public_id=public_id)
    return item_response(user.to_dict())


@router.put("/update/user/{public_id}")
def update_user_by_public_id_router(public_id: str, body: UserUpdate, current_user: dict = Depends(require_module_api(Modules.USERS, "can_update"))):
    """
    Update a user by public ID.
    
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
            "firstname": body.firstname,
            "lastname": body.lastname,
        },
        workflow_type="user_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update user")
    
    return item_response(result.get("data"))


@router.put("/update/user/{public_id}/worker-link")
def update_user_worker_link_router(
    public_id: str,
    body: UserWorkerLinkUpdate,
    current_user: dict = Depends(require_module_api(Modules.USERS, "can_update")),
):
    """Set or clear the User's worker (Employee XOR Vendor) linkage.

    Doesn't route through ProcessEngine — narrow scoped mutation with its own
    audit (User.ModifiedDatetime). Service-layer XOR + sproc-level defense
    catch double-link attempts.
    """
    try:
        updated = UserService().set_worker_link(
            public_id=public_id,
            row_version=body.row_version,
            worker_type=body.worker_type,
            worker_public_id=body.worker_public_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if not updated:
        raise_not_found("User")
    return item_response(updated.to_dict())


@router.delete("/delete/user/{public_id}")
def delete_user_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.USERS, "can_delete"))):
    """
    Delete a user by public ID.
    
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
        workflow_type="user_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete user")
    
    return item_response(result.get("data"))
