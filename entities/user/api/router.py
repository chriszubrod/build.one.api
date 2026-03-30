# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.user.api.schemas import UserCreate, UserUpdate
from entities.user.business.service import UserService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create user")
        )
    
    return result.get("data")


@router.get("/get/users")
def get_users_router(current_user: dict = Depends(require_module_api(Modules.USERS))):
    """
    Read all users.
    """
    users = UserService().read_all()
    return [user.to_dict() for user in users]


@router.get("/get/user/{public_id}")
def get_user_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.USERS))):
    """
    Read a user by public ID.
    """
    user = UserService().read_by_public_id(public_id=public_id)
    return user.to_dict()


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update user")
        )
    
    return result.get("data")


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete user")
        )
    
    return result.get("data")
