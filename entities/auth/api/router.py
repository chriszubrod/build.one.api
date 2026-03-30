# Python Standard Library Imports
from typing import Optional
import secrets

# Third-party Imports
from fastapi import APIRouter, HTTPException, Depends, Response, Request

# Local Imports
from config import Settings
from entities.auth.api.schemas import (
    AuthCreate,
    AuthUpdate,
    AuthLogin,
    AuthSignup,
    AuthRefreshRequest,
    MobileRefreshRequest
)
from entities.auth.business.service import (
    AuthService,
    get_current_user_api,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    UNSAFE_METHODS,
    _require_csrf,
)
from entities.device_token.persistence.repo import DeviceTokenRepository

router = APIRouter(prefix="/api/v1", tags=["auth"])
service = AuthService()

ACCESS_COOKIE_NAME = "token.access_token"
REFRESH_COOKIE_NAME = "token.refresh_token"


def _secure_cookie_enabled() -> bool:
    settings = Settings()
    if settings.debug:
        return False
    return settings.env.lower() not in {"development", "local", "test"}


def _set_auth_cookies(*, response: Response, access_token, refresh_token) -> None:
    secure_cookie = _secure_cookie_enabled()
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access_token.access_token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        max_age=access_token.expires_in,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token.refresh_token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        max_age=refresh_token.expires_in,
        path="/",
    )
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        secure=secure_cookie,
        samesite="lax",
        max_age=refresh_token.expires_in,
        path="/",
    )


def _clear_auth_cookies(*, response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


@router.post("/create/auth")
def create_auth_router(body: AuthCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new auth.
    """
    auth = service.create(
        username=body.username,
        password=body.password
    )
    return auth.to_dict()


@router.get("/get/auth/{public_id}")
def get_auth_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a auth by public ID.
    """
    if current_user.get("sub") != public_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    auth = service.read_by_public_id(public_id=public_id)
    if not auth:
        raise HTTPException(status_code=404, detail="Auth not found.")
    return auth.to_dict()


@router.put("/update/auth/{public_id}")
def update_auth_by_id_router(public_id: str, body: AuthUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update an auth by public ID.
    """
    if current_user.get("sub") != public_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        auth = service.update_by_public_id(public_id=public_id, auth=body)
        return auth.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/update/auth/{public_id}/user-public-id/{user_public_id}")
def update_auth_user_id_router(public_id: str, user_public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Update an auth user ID by public ID.
    """
    if current_user.get("sub") != public_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        auth = service.update_user_id_by_public_id(public_id=public_id, user_public_id=user_public_id)
        return auth.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/delete/auth/{public_id}")
def delete_auth_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete an auth by public ID.
    """
    if current_user.get("sub") != public_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        auth = service.delete_by_public_id(public_id=public_id)
        return auth.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/auth/login")
def login_auth_router(body: AuthLogin, response: Response):
    """
    Login a auth.
    """
    try:
        auth, access_token, refresh_token = service.login(
            username=body.username,
            password=body.password
        )
        _set_auth_cookies(response=response, access_token=access_token, refresh_token=refresh_token)
        return {
            "auth": auth.to_dict(),
            "token": access_token.to_dict(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Login failed.")


@router.post("/signup/auth")
def signup_auth_router(body: AuthSignup, response: Response):
    """
    Signup a auth.
    """
    try:
        auth, access_token, refresh_token = service.signup(
            username=body.username,
            password=body.password,
            confirm_password=body.confirm_password,
            registration_code=body.registration_code
        )
        _set_auth_cookies(response=response, access_token=access_token, refresh_token=refresh_token)
        return {
            "auth": auth.to_dict(),
            "token": access_token.to_dict(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/auth/refresh")
def refresh_token_router(request: Request, response: Response, body: Optional[AuthRefreshRequest] = None):
    """
    Refresh access token using refresh token.
    Implements token rotation for security.
    """
    try:
        if request.cookies.get(REFRESH_COOKIE_NAME):
            _require_csrf(request)
        refresh_token_value = body.refresh_token if body and body.refresh_token else None
        if not refresh_token_value:
            refresh_token_value = request.cookies.get(REFRESH_COOKIE_NAME)
        if not refresh_token_value:
            raise ValueError("Refresh token missing.")
        access_token, refresh_token = service.refresh_access_token(
            refresh_token=refresh_token_value
        )
        _set_auth_cookies(response=response, access_token=access_token, refresh_token=refresh_token)
        return {
            "token": access_token.to_dict(),
        }
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Token refresh failed.")


# ---------------------------------------------------------------------------
# Mobile endpoints — token-based (no cookies, no CSRF)
# ---------------------------------------------------------------------------


@router.post("/mobile/auth/login")
def mobile_login_router(body: AuthLogin):
    """
    Mobile login. Returns access and refresh tokens in the response body.
    Client stores tokens in secure device storage (e.g. iOS Keychain).
    """
    try:
        auth, access_token, refresh_token = service.login(
            username=body.username,
            password=body.password
        )
        return {
            "auth": auth.to_dict(),
            "token": access_token.to_dict(),
            "refresh_token": refresh_token.to_dict(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Login failed.")


@router.post("/mobile/auth/refresh")
def mobile_refresh_router(body: MobileRefreshRequest):
    """
    Mobile token refresh. Accepts refresh token in request body,
    returns new access and refresh tokens (token rotation).
    """
    try:
        access_token, refresh_token = service.refresh_access_token(
            refresh_token=body.refresh_token
        )
        return {
            "token": access_token.to_dict(),
            "refresh_token": refresh_token.to_dict(),
        }
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Token refresh failed.")


@router.post("/mobile/auth/logout")
def mobile_logout_router(body: MobileRefreshRequest, current_user: dict = Depends(get_current_user_api)):
    """
    Mobile logout. Revokes the refresh token server-side.
    Client should discard both tokens from secure storage.
    """
    service.revoke_refresh_token(refresh_token=body.refresh_token)
    return {"message": "Logged out."}


@router.post("/mobile/device-token/register")
async def mobile_register_device_token(
    request: Request,
    current_user=Depends(get_current_user_api),
):
    """
    Register an iOS device token for push notifications.
    Called by the iOS app immediately after login and when
    APNs issues a new token (application(_:didRegisterForRemoteNotificationsWithDeviceToken:)).
    """
    body = await request.json()
    device_token  = body.get("device_token", "").strip()
    app_bundle_id = body.get("app_bundle_id", "one.build.app").strip()

    if not device_token:
        raise HTTPException(status_code=422, detail="device_token is required.")

    from entities.auth.business.service import AuthService
    auth_service = AuthService()
    user = auth_service.read_by_public_id(current_user["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    repo   = DeviceTokenRepository()
    result = repo.upsert(
        user_id=      user.id,
        device_token= device_token,
        app_bundle_id=app_bundle_id,
    )
    return {"registered": True, "public_id": str(result.public_id)}


@router.post("/mobile/device-token/deactivate")
async def mobile_deactivate_device_token(
    request: Request,
    current_user=Depends(get_current_user_api),
):
    """
    Deactivate a device token on logout.
    Prevents push notifications being sent to a logged-out device.
    """
    body = await request.json()
    device_token = body.get("device_token", "").strip()

    if not device_token:
        raise HTTPException(status_code=422, detail="device_token is required.")

    repo = DeviceTokenRepository()
    repo.deactivate(device_token)
    return {"deactivated": True}
