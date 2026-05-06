# Python Standard Library Imports
import asyncio
import json
import logging
from typing import Optional
import secrets

# Third-party Imports
from fastapi import APIRouter, HTTPException, Depends, Response, Request
from fastapi.responses import StreamingResponse

# Local Imports
from config import Settings
from entities.auth.api.schemas import (
    AdminSetCredentials,
    AuthCreate,
    AuthUpdate,
    AuthLogin,
    AuthSignup,
    AuthRefreshRequest,
    MobileRefreshRequest,
    SwitchCompanyRequest,
)
from entities.auth.business.service import (
    AuthService,
    get_current_user_api,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    UNSAFE_METHODS,
    _require_csrf,
)
from entities.module.business.service import ModuleService
from entities.role.business.service import RoleService
from entities.role_module.business.service import RoleModuleService
from entities.user.business.service import UserService
from entities.user_module.business.service import UserModuleService
from entities.user_project.business.service import UserProjectService
from entities.user_role.business.service import UserRoleService
from shared.api.responses import item_response, raise_not_found
from shared.authz.companies import (
    list_accessible_companies,
    resolve_active_company_for_user,
)
from shared.profile_events import profile_event_subscription, publish_profile_changed
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

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
    return item_response(auth.to_dict())


@router.get("/get/auth/{public_id}")
def get_auth_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a auth by public ID.
    """
    if current_user.get("sub") != public_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    auth = service.read_by_public_id(public_id=public_id)
    if not auth:
        raise_not_found("Auth")
    return item_response(auth.to_dict())


@router.put("/update/auth/{public_id}")
def update_auth_by_id_router(public_id: str, body: AuthUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update an auth by public ID.
    """
    if current_user.get("sub") != public_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        auth = service.update_by_public_id(public_id=public_id, auth=body)
        return item_response(auth.to_dict())
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
        return item_response(auth.to_dict())
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
        return item_response(auth.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/admin/auth/by-user/{user_public_id}")
def admin_get_auth_by_user_public_id_router(
    user_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.USERS, "can_read")),
):
    """
    Admin: read the credential summary for a user (username + has_auth).
    Never returns the password hash.
    """
    user = UserService().read_by_public_id(public_id=user_public_id)
    if not user:
        raise_not_found("User")
    auth = service.read_by_user_id(user_id=user.id)
    if not auth:
        return item_response({"username": None, "has_auth": False})
    return item_response({"username": auth.username, "has_auth": True})


@router.post("/admin/auth/set-credentials/{user_public_id}")
def admin_set_credentials_router(
    user_public_id: str,
    body: AdminSetCredentials,
    current_user: dict = Depends(require_module_api(Modules.USERS, "can_update")),
):
    """
    Admin: create-or-update the Auth row (username + password) for a user.
    """
    try:
        auth = service.set_credentials_for_user(
            user_public_id=user_public_id,
            username=body.username,
            password=body.password,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        "admin set credentials: actor_sub=%s target_user_public_id=%s auth_public_id=%s",
        current_user.get("sub"),
        user_public_id,
        auth.public_id,
    )
    if auth.user_id is not None:
        publish_profile_changed(auth.user_id)
    return item_response({"username": auth.username, "has_auth": True})


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
        return item_response({
            "auth": auth.to_dict(),
            "token": access_token.to_dict(),
        })
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
        return item_response({
            "auth": auth.to_dict(),
            "token": access_token.to_dict(),
        })
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
        return item_response({
            "token": access_token.to_dict(),
        })
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Token refresh failed.")


@router.post("/auth/switch-company")
def switch_company_router(
    body: SwitchCompanyRequest,
    response: Response,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Switch the caller's active Company. Validates membership (or
    system-admin bypass), persists `User.LastCompanyId`, and re-mints
    access + refresh tokens carrying the new `cid`. Old tokens stay
    valid until they expire — there is no server-side revocation on
    switch (token rotation handles steady-state).
    """
    user_sub = current_user.get("sub")
    if not user_sub:
        raise HTTPException(status_code=401, detail="Invalid session.")
    try:
        auth, access_token, refresh_token, active_company = service.switch_active_company(
            user_sub=user_sub,
            company_public_id=body.company_public_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _set_auth_cookies(
        response=response, access_token=access_token, refresh_token=refresh_token
    )
    return item_response({
        "active_company": {
            "public_id": active_company.public_id,
            "name": active_company.name,
            "organization": (
                {
                    "public_id": active_company.organization_public_id,
                    "name": active_company.organization_name,
                }
                if active_company.organization_public_id
                else None
            ),
        },
        "token": access_token.to_dict(),
    })


@router.post("/mobile/auth/switch-company")
def mobile_switch_company_router(
    body: SwitchCompanyRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Mobile variant — same as `/auth/switch-company` but returns both
    access and refresh tokens in the response body (no cookies, no CSRF).
    """
    user_sub = current_user.get("sub")
    if not user_sub:
        raise HTTPException(status_code=401, detail="Invalid session.")
    try:
        auth, access_token, refresh_token, active_company = service.switch_active_company(
            user_sub=user_sub,
            company_public_id=body.company_public_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return item_response({
        "active_company": {
            "public_id": active_company.public_id,
            "name": active_company.name,
            "organization": (
                {
                    "public_id": active_company.organization_public_id,
                    "name": active_company.organization_name,
                }
                if active_company.organization_public_id
                else None
            ),
        },
        "token": access_token.to_dict(),
        "refresh_token": refresh_token.to_dict(),
    })


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
        return item_response({
            "auth": auth.to_dict(),
            "token": access_token.to_dict(),
            "refresh_token": refresh_token.to_dict(),
        })
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
        return item_response({
            "token": access_token.to_dict(),
            "refresh_token": refresh_token.to_dict(),
        })
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
    return item_response({"message": "Logged out."})


# ---------------------------------------------------------------------------
# Profile: /me + /me/changes SSE
# ---------------------------------------------------------------------------


def _resolve_me_payload(user_sub: str) -> dict:
    """
    Build the full { user, auth, role, is_admin, modules[] } payload for
    the current user. System admins (`User.IsSystemAdmin = 1`) get every
    module flagged with every permission. Other users get the OR-union of
    every UserRole row for the active Company, layered with UserModule
    additive read-only grants.
    """
    auth = AuthService().read_by_public_id(public_id=user_sub)
    if auth is None or auth.user_id is None:
        raise HTTPException(status_code=404, detail="Auth profile not found.")

    user = UserService().read_by_id(id=auth.user_id)
    is_system_admin = bool(getattr(user, "is_system_admin", False)) if user else False
    active_company_obj = (
        resolve_active_company_for_user(auth.user_id) if auth.user_id else None
    )
    active_company_id = active_company_obj.id if active_company_obj else None

    permissions_by_module: dict[int, dict] = {}

    # Multi-role UNION across UserRole rows for the active Company.
    if not is_system_admin and active_company_id is not None:
        user_roles = UserRoleService().read_all_by_user_id_and_company_id(
            user_id=auth.user_id, company_id=active_company_id
        )
        role_module_service = RoleModuleService()
        for ur in user_roles:
            role_modules = role_module_service.read_all_by_role_id(role_id=ur.role_id)
            for rm in role_modules:
                existing = permissions_by_module.get(rm.module_id)
                if existing is None:
                    permissions_by_module[rm.module_id] = {
                        "can_create": bool(rm.can_create),
                        "can_read": bool(rm.can_read),
                        "can_update": bool(rm.can_update),
                        "can_delete": bool(rm.can_delete),
                        "can_submit": bool(rm.can_submit),
                        "can_approve": bool(rm.can_approve),
                        "can_complete": bool(rm.can_complete),
                    }
                else:
                    existing["can_create"] = existing["can_create"] or bool(rm.can_create)
                    existing["can_read"] = existing["can_read"] or bool(rm.can_read)
                    existing["can_update"] = existing["can_update"] or bool(rm.can_update)
                    existing["can_delete"] = existing["can_delete"] or bool(rm.can_delete)
                    existing["can_submit"] = existing["can_submit"] or bool(rm.can_submit)
                    existing["can_approve"] = existing["can_approve"] or bool(rm.can_approve)
                    existing["can_complete"] = existing["can_complete"] or bool(rm.can_complete)

        # Layer in additive UserModule grants — read-only, never downgrades a
        # role-granted module.
        user_modules = UserModuleService().read_all_by_user_id_and_company_id(
            user_id=auth.user_id, company_id=active_company_id
        )
        for um in user_modules:
            if um.module_id in permissions_by_module:
                continue
            permissions_by_module[um.module_id] = {
                "can_read": True,
                "can_create": False,
                "can_update": False,
                "can_delete": False,
                "can_submit": False,
                "can_approve": False,
                "can_complete": False,
            }

    # Surface a representative role for legacy UI bits that still display
    # one — pick the first matched UserRole row. Phase 4 will replace this
    # with a multi-role list in the payload.
    role_dict = None
    if not is_system_admin and active_company_id is not None:
        primary_role = next(iter(
            UserRoleService().read_all_by_user_id_and_company_id(
                user_id=auth.user_id, company_id=active_company_id
            )
        ), None)
        if primary_role:
            role = RoleService().read_by_id(id=primary_role.role_id)
            if role:
                role_dict = {"public_id": role.public_id, "name": role.name}

    all_modules = ModuleService().read_all()
    modules_payload = []
    for m in all_modules:
        if is_system_admin:
            perms = {p: True for p in (
                "can_create", "can_read", "can_update", "can_delete",
                "can_submit", "can_approve", "can_complete",
            )}
        else:
            perms = permissions_by_module.get(m.id, {p: False for p in (
                "can_create", "can_read", "can_update", "can_delete",
                "can_submit", "can_approve", "can_complete",
            )})
        modules_payload.append({
            "public_id": m.public_id,
            "name": m.name,
            "route": m.route,
            **perms,
        })

    user_projects = UserProjectService().read_by_user_id(user_id=auth.user_id)
    accessible_project_ids = sorted({up.project_id for up in user_projects if up.project_id is not None})

    # Phase 0 — surface the user's tenant context so future React/iOS
    # pickers can render without an extra round-trip.
    accessible_companies = list_accessible_companies(auth.user_id) if auth.user_id else []
    companies_payload = [
        {
            "public_id": c.public_id,
            "name": c.name,
            "organization": (
                {"public_id": c.organization_public_id, "name": c.organization_name}
                if c.organization_public_id
                else None
            ),
        }
        for c in accessible_companies
    ]
    seen_org_ids: set[str] = set()
    organizations_payload: list[dict] = []
    for c in accessible_companies:
        if c.organization_public_id and c.organization_public_id not in seen_org_ids:
            seen_org_ids.add(c.organization_public_id)
            organizations_payload.append({
                "public_id": c.organization_public_id,
                "name": c.organization_name,
            })

    active_company_payload = (
        {
            "public_id": active_company_obj.public_id,
            "name": active_company_obj.name,
            "organization": (
                {
                    "public_id": active_company_obj.organization_public_id,
                    "name": active_company_obj.organization_name,
                }
                if active_company_obj.organization_public_id
                else None
            ),
        }
        if active_company_obj
        else None
    )

    return {
        "auth": {"public_id": auth.public_id, "username": auth.username},
        "user": user.to_dict() if user else None,
        "role": role_dict,
        "is_admin": is_system_admin,
        "is_system_admin": is_system_admin,
        "modules": modules_payload,
        "accessible_project_ids": accessible_project_ids,
        "companies": companies_payload,
        "organizations": organizations_payload,
        "active_company": active_company_payload,
    }


@router.get("/auth/me")
def get_me_router(current_user: dict = Depends(get_current_user_api)):
    """
    Current-user profile: identity + role + per-module permissions.
    Clients cache the result under ['me']; invalidate on SSE `profile_changed`.
    """
    payload = _resolve_me_payload(current_user["sub"])
    return item_response(payload)


@router.get("/auth/me/changes")
async def stream_me_changes_router(
    request: Request,
    current_user: dict = Depends(get_current_user_api),
):
    """
    SSE stream of profile-change events for the current user. Emits
    `profile_changed` when an admin mutates the caller's UserRole, or
    when a RoleModule under the caller's role changes. Clients react by
    invalidating their cached `/auth/me` query.
    """
    user_sub = current_user["sub"]
    auth = AuthService().read_by_public_id(public_id=user_sub)
    if auth is None or auth.user_id is None:
        raise HTTPException(status_code=404, detail="Auth profile not found.")
    user_id = auth.user_id

    async def _generator():
        async with profile_event_subscription(user_id) as queue:
            yield ": connected\n\n"  # initial comment frame so clients see an open stream
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"

    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_generator(), media_type="text/event-stream", headers=headers)


