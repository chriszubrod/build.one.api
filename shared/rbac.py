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

Permission levels (from RoleModule):
    can_read, can_create, can_update, can_delete,
    can_submit, can_approve, can_complete

Resolver model (Phase 2 — Access Control Rebuild)
-------------------------------------------------
- `User.IsSystemAdmin = 1` bypasses every module check (no role lookup, no
  Company scoping). Replaces the old role-name "admin" magic-string check.
- For non-system-admin users, permissions are the OR-union of every
  `UserRole` row for the (user, active Company) pair, joined to `RoleModule`
  per role.
- `UserModule` rows are layered on top as additive read-only grants
  (can_read=True, others False). Never downgrades a role-granted module.
- The cache is keyed by `(user_sub, company_id)` so switching Companies
  resolves a fresh permission map.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from fastapi import Depends, HTTPException

from types import SimpleNamespace

from entities.auth.business.service import get_current_user_api
from entities.module.business.service import ModuleService
from entities.role_module.business.service import RoleModuleService
from entities.user_module.business.service import UserModuleService
from entities.user_role.business.service import UserRoleService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS = 300          # 5 minutes


# Sentinel marker for system-admin permission maps. Typed object — avoids
# the magic-string approach the old `_ADMIN_SENTINEL` used.
class _SystemAdminGrant:
    """Singleton marker indicating a user has the IsSystemAdmin bypass."""
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — diagnostic only
        return "<SystemAdminGrant>"


SYSTEM_ADMIN_GRANT = _SystemAdminGrant()


# Valid permission attributes on RoleModule
VALID_PERMISSIONS = frozenset({
    "can_create", "can_read", "can_update", "can_delete",
    "can_submit", "can_approve", "can_complete",
})

# ---------------------------------------------------------------------------
# Permission cache
# ---------------------------------------------------------------------------
# Structure: { (user_sub, company_id): (timestamp, permissions) }
#   permissions = SYSTEM_ADMIN_GRANT                  for system admins
#   permissions = { module_name: SimpleNamespace }    for normal users
#   permissions = None                                for users with no access
#
# company_id is None for system-admin entries (admins bypass Company scoping).
# ---------------------------------------------------------------------------

_CacheKey = tuple[str, Optional[int]]
_permission_cache: dict[_CacheKey, tuple[float, Optional[object]]] = {}
_cache_lock = threading.Lock()


def _cache_key_for(user_sub: str, *, is_system_admin: bool, company_id: Optional[int]) -> _CacheKey:
    """
    System admins are keyed without a Company so a single cache entry
    serves them across Company switches. Regular users are keyed by
    (sub, company_id) — switching Companies resolves a fresh entry.
    """
    if is_system_admin:
        return (user_sub, None)
    return (user_sub, company_id)


def _get_user_permissions(current_user: dict) -> Optional[object]:
    """
    Resolve and cache the permission map for the current request.

    Returns:
        SYSTEM_ADMIN_GRANT             — if `current_user["is_system_admin"]`
        { module_name: SimpleNamespace } — for normal users with permissions
        None                           — if the user has no access
    """
    user_sub = current_user.get("sub")
    if not user_sub:
        return None

    is_system_admin = bool(current_user.get("is_system_admin"))
    company_id = current_user.get("company_id")
    user_id = current_user.get("user_id")

    cache_key = _cache_key_for(
        user_sub, is_system_admin=is_system_admin, company_id=company_id
    )
    now = time.time()

    with _cache_lock:
        if cache_key in _permission_cache:
            cached_time, cached_perms = _permission_cache[cache_key]
            if now - cached_time < CACHE_TTL_SECONDS:
                return cached_perms

    perms = _resolve_permissions_from_db(
        user_id=user_id,
        company_id=company_id,
        is_system_admin=is_system_admin,
    )

    with _cache_lock:
        _permission_cache[cache_key] = (time.time(), perms)

    return perms


def _resolve_permissions_from_db(
    *,
    user_id: Optional[int],
    company_id: Optional[int],
    is_system_admin: bool,
) -> Optional[object]:
    """
    Full resolution chain:
        IsSystemAdmin            -> SYSTEM_ADMIN_GRANT
        UserRole(user, company)+ -> RoleModule UNION across roles
        UserModule(user, company)+ additive read-only grant
    """
    if is_system_admin:
        return SYSTEM_ADMIN_GRANT

    # Non-admin path requires both an internal user_id and an active Company.
    # Either missing -> deny.
    if user_id is None or company_id is None:
        return None

    user_roles = UserRoleService().read_all_by_user_id_and_company_id(
        user_id=user_id, company_id=company_id
    )

    perms: dict[str, SimpleNamespace] = {}

    if user_roles:
        all_modules = ModuleService().read_all()
        module_id_to_name = {m.id: m.name for m in all_modules}
        role_module_service = RoleModuleService()

        for ur in user_roles:
            role_modules = role_module_service.read_all_by_role_id(role_id=ur.role_id)
            for rm in role_modules:
                module_name = module_id_to_name.get(rm.module_id)
                if not module_name:
                    continue
                existing = perms.get(module_name)
                if existing is None:
                    perms[module_name] = SimpleNamespace(
                        can_create=bool(rm.can_create),
                        can_read=bool(rm.can_read),
                        can_update=bool(rm.can_update),
                        can_delete=bool(rm.can_delete),
                        can_submit=bool(rm.can_submit),
                        can_approve=bool(rm.can_approve),
                        can_complete=bool(rm.can_complete),
                    )
                else:
                    existing.can_create = existing.can_create or bool(rm.can_create)
                    existing.can_read = existing.can_read or bool(rm.can_read)
                    existing.can_update = existing.can_update or bool(rm.can_update)
                    existing.can_delete = existing.can_delete or bool(rm.can_delete)
                    existing.can_submit = existing.can_submit or bool(rm.can_submit)
                    existing.can_approve = existing.can_approve or bool(rm.can_approve)
                    existing.can_complete = existing.can_complete or bool(rm.can_complete)

    # Layer in additive UserModule grants — read-only, never downgrades a
    # role-granted module.
    user_modules = UserModuleService().read_all_by_user_id_and_company_id(
        user_id=user_id, company_id=company_id
    )
    if user_modules:
        if not perms:
            all_modules = ModuleService().read_all()
            module_id_to_name = {m.id: m.name for m in all_modules}
        for um in user_modules:
            module_name = module_id_to_name.get(um.module_id)
            if not module_name or module_name in perms:
                continue
            perms[module_name] = SimpleNamespace(
                can_read=True,
                can_create=False,
                can_update=False,
                can_delete=False,
                can_submit=False,
                can_approve=False,
                can_complete=False,
            )

    if not perms:
        return None

    return perms


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def invalidate_user_cache(user_sub: str) -> None:
    """
    Remove every cached entry for the given user across all Companies.
    Called on UserRole / UserModule mutation, role grant change, and
    Company switch.
    """
    with _cache_lock:
        stale = [k for k in _permission_cache if k[0] == user_sub]
        for key in stale:
            _permission_cache.pop(key, None)


def invalidate_user_company_cache(user_sub: str, company_id: Optional[int]) -> None:
    """Targeted invalidation for a single (user, Company) cache entry."""
    with _cache_lock:
        _permission_cache.pop((user_sub, company_id), None)


def invalidate_all_caches() -> None:
    """Clear the entire permission cache (e.g. after bulk role/module changes)."""
    with _cache_lock:
        _permission_cache.clear()


# ---------------------------------------------------------------------------
# Core enforcement
# ---------------------------------------------------------------------------

def _enforce_module_permission(
    current_user: dict,
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

    perms = _get_user_permissions(current_user)

    # System-admin bypass — short-circuit before any module lookup.
    if perms is SYSTEM_ADMIN_GRANT:
        return

    # No role assigned + no UserModule grant
    if perms is None:
        raise HTTPException(
            status_code=403,
            detail="Access denied — no role assigned.",
        )

    # Module not assigned to role
    role_module = perms.get(module_name) if isinstance(perms, dict) else None
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
            current_user=current_user,
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
    True iff the current user has IsSystemAdmin set on their User row.
    Reads the flag directly from the enriched JWT payload — no DB call.
    """
    return bool(current_user.get("is_system_admin"))


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
