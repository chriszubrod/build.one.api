# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.user.api.schemas import UserCreate, UserUpdate
from modules.user.business.service import UserService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "user"])


@router.post("/create/user")
def create_user_router(body: UserCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new user.
    """
    user = UserService().create(
        firstname=body.firstname,
        lastname=body.lastname
    )
    return user.to_dict()


@router.get("/get/users")
def get_users_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all users.
    """
    users = UserService().read_all()
    return [user.to_dict() for user in users]


@router.get("/get/user/{public_id}")
def get_user_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a user by public ID.
    """
    user = UserService().read_by_public_id(public_id=public_id)
    return user.to_dict()


@router.put("/update/user/{public_id}")
def update_user_by_public_id_router(public_id: str, body: UserUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a user by public ID.
    """
    user = UserService().update_by_public_id(public_id=public_id, user=body)
    return user.to_dict()


@router.delete("/delete/user/{public_id}")
def delete_user_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a user by public ID.
    """
    user = UserService().delete_by_public_id(public_id=public_id)
    return user.to_dict()
