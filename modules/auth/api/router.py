# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, HTTPException

# Local Imports
from modules.auth.api.schemas import (
    AuthCreate,
    AuthUpdate,
    AuthLogin,
    AuthSignup
)
from modules.auth.business.service import (
    AuthService,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])
service = AuthService()


@router.post("/create/auth")
def create_auth_router(body: AuthCreate):
    """
    Create a new auth.
    """
    auth = service.create(
        username=body.username,
        password_hash=body.password
    )
    return auth.to_dict()


@router.get("/get/auth/{public_id}")
def get_auth_by_public_id_router(public_id: str):
    """
    Read a auth by public ID.
    """
    auth = service.read_by_public_id(public_id=public_id)
    return auth.to_dict()


@router.put("/update/auth/{public_id}")
def update_auth_by_id_router(public_id: str, body: AuthUpdate):
    """
    Update a auth by ID.
    """
    auth = service.update_by_public_id(public_id=public_id, auth=body)
    return auth.to_dict()


@router.delete("/delete/auth/{public_id}")
def delete_auth_by_public_id_router(public_id: str):
    """
    Soft delete a auth by ID.
    """
    auth = service.delete_by_public_id(public_id=public_id)
    return auth.to_dict()


@router.post("/auth/login")
def login_auth_router(body: AuthLogin):
    """
    Login a auth.
    """
    try:
        auth, token = service.login(
            username=body.username,
            password=body.password
        )
        return {
            "auth": auth.to_dict(),
            "token": token.to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/auth/signup")
def signup_auth_router(body: AuthSignup):
    """
    Signup a auth.
    """
    try:
        auth = service.signup(
            username=body.username,
            password=body.password,
            confirm_password=body.confirm_password
        )
        return auth.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
