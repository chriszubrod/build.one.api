# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, HTTPException, Depends

# Local Imports
from modules.auth.api.schemas import (
    AuthCreate,
    AuthUpdate,
    AuthUpdateUserId,
    AuthLogin,
    AuthSignup,
    AuthRefreshRequest
)
from modules.auth.business.service import (
    AuthService,
    get_current_user_api,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])
service = AuthService()


@router.post("/create/auth")
def create_auth_router(body: AuthCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new auth.
    """
    auth = service.create(
        username=body.username,
        password_hash=body.password
    )
    return auth.to_dict()


@router.get("/get/auth/{public_id}")
def get_auth_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a auth by public ID.
    """
    auth = service.read_by_public_id(public_id=public_id)
    return auth.to_dict()


@router.put("/update/auth/{public_id}")
def update_auth_by_id_router(public_id: str, body: AuthUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a auth by ID.
    """
    auth = service.update_by_public_id(public_id=public_id, auth=body)
    return auth.to_dict()


@router.put("/update/auth/{public_id}/user-public-id/{user_public_id}")
def update_auth_user_id_router(public_id: str, user_public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Update a auth user ID by public ID.
    """
    auth = service.update_user_id_by_public_id(public_id=public_id, user_public_id=user_public_id)
    return auth.to_dict()


@router.delete("/delete/auth/{public_id}")
def delete_auth_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
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
        auth, access_token, refresh_token = service.login(
            username=body.username,
            password=body.password
        )
        return {
            "auth": auth.to_dict(),
            "token": access_token.to_dict(),
            "refresh_token": refresh_token.to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Catch all other exceptions (database errors, etc.) and return JSON
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/signup/auth")
def signup_auth_router(body: AuthSignup):
    """
    Signup a auth.
    """
    try:
        auth, access_token, refresh_token = service.signup(
            username=body.username,
            password=body.password,
            confirm_password=body.confirm_password
        )
        return {
            "auth": auth.to_dict(),
            "token": access_token.to_dict(),
            "refresh_token": refresh_token.to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/auth/refresh")
def refresh_token_router(body: AuthRefreshRequest):
    """
    Refresh access token using refresh token.
    Implements token rotation for security.
    """
    try:
        access_token, refresh_token = service.refresh_access_token(
            refresh_token=body.refresh_token
        )
        return {
            "token": access_token.to_dict(),
            "refresh_token": refresh_token.to_dict()
        }
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
