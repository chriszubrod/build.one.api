# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.user_module.api.schemas import UserModuleCreate, UserModuleUpdate
from entities.user_module.business.service import UserModuleService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "user_module"])


@router.post("/create/user_module")
def create_user_module_router(body: UserModuleCreate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_create"))):
    """
    Create a new user module.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "user_id": body.user_id,
            "module_id": body.module_id,
        },
        workflow_type="user_module_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create user module")
        )

    return result.get("data")


@router.get("/get/user_modules")
def get_user_modules_router(current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read all user modules.
    """
    user_modules = UserModuleService().read_all()
    return [user_module.to_dict() for user_module in user_modules]


@router.get("/get/user_module/{public_id}")
def get_user_module_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read a user module by public ID.
    """
    user_module = UserModuleService().read_by_public_id(public_id=public_id)
    return user_module.to_dict()


@router.get("/get/user_modules/user/{user_id}")
def get_user_modules_by_user_id_router(user_id: int, current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read all user modules by user ID.
    """
    user_modules = UserModuleService().read_all_by_user_id(user_id=user_id)
    return [user_module.to_dict() for user_module in user_modules]


@router.put("/update/user_module/{public_id}")
def update_user_module_by_public_id_router(public_id: str, body: UserModuleUpdate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_update"))):
    """
    Update a user module by public ID.

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
            "user_id": body.user_id,
            "module_id": body.module_id,
        },
        workflow_type="user_module_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update user module")
        )

    return result.get("data")


@router.delete("/delete/user_module/{public_id}")
def delete_user_module_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_delete"))):
    """
    Delete a user module by public ID.

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
        workflow_type="user_module_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete user module")
        )

    return result.get("data")
