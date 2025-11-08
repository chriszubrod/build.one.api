# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.user_role.api.schemas import UserRoleCreate, UserRoleUpdate
from modules.user_role.business.service import UserRoleService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "user_role"])


@router.post("/create/user_role")
def create_user_role_router(body: UserRoleCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new user role.
    """
    user_role = UserRoleService().create(
        user_id=body.user_id,
        role_id=body.role_id
    )
    return user_role.to_dict()


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
    """
    user_role = UserRoleService().update_by_public_id(public_id=public_id, user_role=body)
    return user_role.to_dict()


@router.delete("/delete/user_role/{public_id}")
def delete_user_role_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a user role by public ID.
    """
    user_role = UserRoleService().delete_by_public_id(public_id=public_id)
    return user_role.to_dict()
