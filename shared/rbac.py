"""
RBAC Authorization
==================
FastAPI dependency factories that enforce module-level permissions
with cached permission resolution.

Usage on API routers:
    from shared.rbac import require_module_api
    from shared.rbac_constants import Modules

    @router.get("/bills")
    async def list_bills(current_user=Depends(require_module_api(Modules.BILLS))):
        ...

    @router.post("/bills")
    async def create_bill(current_user=Depends(require_module_api(Modules.BILLS, "can_create"))):
        ...

Usage on web controllers:
    from shared.rbac import require_module_web
    from shared.rbac_constants import Modules

    @router.get("/bill/list")
    async def bill_list(request: Request, current_user=Depends(require_module_web(Modules.BILLS))):
        ...

Permission levels (from RoleModule):
    can_read, can_create, can_update, can_delete,
    can_submit, can_approve, can_complete
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from fastapi import Depends, HTTPException

from entities.auth.business.service import (
    get_current_user_api,
    get_current_user_web,
    AuthService,
)
from entities.module.business.service import ModuleService
from entities.role.business.service import RoleService
from entities.role_module.business.service import RoleModuleService
from entities.user_role.business.service import UserRoleService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS = 300          # 5 minutes
_ADMIN_SENTINEL = "__admin__"

# Valid permission attributes on RoleModule
VALID_PERMISSIONS = frozenset({
    "can_create", "can_read", "can_update", "can_delete",
    "can_submit", "can_approve", "can_complete",
})

# ---------------------------------------------------------------------------
# Permission cache
# ---------------------------------------------------------------------------
# Structure: { user_sub: (timestamp, permissions) }
#   permissions = { module_name: RoleModule } for normal users
#   permissions = { "__admin__": True }       for admin users
#   permissions = None                        for users with no role
# ---------------------------------------------------------------------------

_permission_cache: dict[str, tuple[float, Optional[dict]]] = {}
_cache_lock = threading.Lock()


def _get_user_permissions(user_sub: str) -> Optional[dict]:
    """
    Resolve and cache the full permission map for a user.

    Returns:
        { module_name: RoleModule }  — for normal users
        { "__admin__": True }        — for admin users
        None                         — no role assigned
    """
    now = time.time()

    with _cache_lock:
        if user_sub in _permission_cache:
            cached_time, cached_perms = _permission_cache[user_sub]
            if now - cached_time < CACHE_TTL_SECONDS:
                return cached_perms

    # Cache miss — resolve from DB (outside lock to avoid holding it during IO)
    perms = _resolve_permissions_from_db(user_sub)

    with _cache_lock:
        _permission_cache[user_sub] = (time.time(), perms)

    return perms


def _resolve_permissions_from_db(user_sub: str) -> Optional[dict]:
    """
    Full resolution chain:
        JWT sub (public_id) -> Auth -> user_id
            -> UserRole -> role_id
                -> Role (admin check)
                -> RoleModule + Module (permission map)
    """
    # Resolve user_id
    auth = AuthService().read_by_public_id(public_id=user_sub)
    if not auth or not auth.user_id:
        return None

    # Resolve role
    user_role = UserRoleService().read_by_user_id(user_id=auth.user_id)
    if not user_role:
        return None

    # Admin bypass
    role = RoleService().read_by_id(id=user_role.role_id)
    if role and role.name and role.name.strip().lower() == "admin":
        return {_ADMIN_SENTINEL: True}

    # Build { module_name: RoleModule } map
    role_modules = RoleModuleService().read_all_by_role_id(role_id=user_role.role_id)
    all_modules = ModuleService().read_all()
    module_id_to_name = {m.id: m.name for m in all_modules}

    perms = {}
    for rm in role_modules:
        name = module_id_to_name.get(rm.module_id)
        if name:
            perms[name] = rm

    return perms


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def invalidate_user_cache(user_sub: str) -> None:
    """Remove a specific user's cached permissions (e.g. after role change)."""
    with _cache_lock:
        _permission_cache.pop(user_sub, None)


def invalidate_all_caches() -> None:
    """Clear the entire permission cache (e.g. after bulk role/module changes)."""
    with _cache_lock:
        _permission_cache.clear()


# ---------------------------------------------------------------------------
# Core enforcement
# ---------------------------------------------------------------------------

def _enforce_module_permission(
    user_sub: str,
    module_name: str,
    permission: str,
) -> None:
    """
    Check whether the user has the specified permission on the given module.
    Raises HTTPException(403) on denial.
    """
    if permission not in VALID_PERMISSIONS:
        raise ValueError(
            f"Invalid permission '{permission}'. "
            f"Must be one of: {sorted(VALID_PERMISSIONS)}"
        )

    perms = _get_user_permissions(user_sub)

    # No role assigned
    if perms is None:
        raise HTTPException(
            status_code=403,
            detail="Access denied — no role assigned.",
        )

    # Admin bypass
    if _ADMIN_SENTINEL in perms:
        return

    # Module not assigned to role
    role_module = perms.get(module_name)
    if not role_module:
        raise HTTPException(
            status_code=403,
            detail="Access denied — module not assigned to your role.",
        )

    # Permission not granted
    if not getattr(role_module, permission, False):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Access denied — your role does not have "
                f"'{permission}' permission on '{module_name}'."
            ),
        )


# ---------------------------------------------------------------------------
# Public dependency factories
# ---------------------------------------------------------------------------

def require_module_api(module_name: str, permission: str = "can_read"):
    """
    FastAPI dependency for API routes.
    Wraps get_current_user_api + cached RBAC check.

    Usage:
        current_user=Depends(require_module_api(Modules.BILLS, "can_create"))
    """
    def _dependency(current_user=Depends(get_current_user_api)):
        _enforce_module_permission(
            user_sub=current_user["sub"],
            module_name=module_name,
            permission=permission,
        )
        return current_user
    return _dependency


def require_module_web(module_name: str, permission: str = "can_read"):
    """
    FastAPI dependency for web (Jinja2) routes.
    Wraps get_current_user_web + cached RBAC check.

    Usage:
        current_user=Depends(require_module_web(Modules.BILLS))
    """
    def _dependency(current_user=Depends(get_current_user_web)):
        _enforce_module_permission(
            user_sub=current_user["sub"],
            module_name=module_name,
            permission=permission,
        )
        return current_user
    return _dependency


# ---------------------------------------------------------------------------
# Admin check helper
# ---------------------------------------------------------------------------

def is_admin_user(current_user: dict) -> bool:
    """
    Check if the current user has the admin role.
    Uses the cached permission map — no extra DB call.
    """
    user_sub = current_user.get("sub")
    if not user_sub:
        return False
    perms = _get_user_permissions(user_sub)
    return perms is not None and _ADMIN_SENTINEL in perms


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

def validate_module_constants() -> list[str]:
    """
    Compare Modules constants against dbo.[Module] rows.
    Returns a list of warnings for any constant that has no matching
    database record. Call during app startup to catch mismatches early.

    Usage in app.py:
        @app.on_event("startup")
        async def startup():
            from shared.rbac import validate_module_constants
            warnings = validate_module_constants()
            for w in warnings:
                logger.warning(w)
    """
    from shared.rbac_constants import Modules

    db_modules = ModuleService().read_all()
    db_names = {m.name for m in db_modules}

    constant_names = {
        v for k, v in vars(Modules).items()
        if not k.startswith("_") and isinstance(v, str)
    }

    warnings = []
    for name in sorted(constant_names - db_names):
        warnings.append(
            f"RBAC WARNING: Module constant '{name}' has no matching "
            f"dbo.[Module] row. RBAC checks for this module will deny all users."
        )
    for name in sorted(db_names - constant_names):
        warnings.append(
            f"RBAC INFO: dbo.[Module] row '{name}' has no matching "
            f"constant in shared/rbac_constants.py."
        )

    return warnings
