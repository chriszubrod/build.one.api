"""
Per-request access-control ContextVars.

Populated in the auth dependency (`get_current_user_api`); read by
services and repos that need the active subject + tenant. Phase 0
populates the vars but enforcement only kicks in once the resolver
in Phase 2 starts using them.

These are ContextVars (not threadlocals) so async tasks share the
context of the request that spawned them, which matters for FastAPI's
`asyncio.to_thread` and any background tasks scheduled inside a
request.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# Active subject (User.Id) for the current request. None when the
# request is unauthenticated or the auth dependency hasn't run yet.
current_user_id: ContextVar[Optional[int]] = ContextVar(
    "current_user_id", default=None
)

# Active tenant (Company.Id) for the current request. None during
# Phase 0 grace-window fallback when no `cid` claim is present and
# the user has zero accessible Companies (system admins).
current_company_id: ContextVar[Optional[int]] = ContextVar(
    "current_company_id", default=None
)

# True if the active subject is a Build.One staff super-admin
# (User.IsSystemAdmin = 1). Used to bypass tenant + module checks.
current_is_system_admin: ContextVar[bool] = ContextVar(
    "current_is_system_admin", default=False
)


def set_authz_context(
    *,
    user_id: Optional[int],
    company_id: Optional[int],
    is_system_admin: bool,
) -> None:
    """
    Populate the per-request ContextVars. Called by the auth dependency
    after a JWT is verified. Safe to call multiple times within a request
    (e.g. after a switch-company refresh).
    """
    current_user_id.set(user_id)
    current_company_id.set(company_id)
    current_is_system_admin.set(bool(is_system_admin))


def clear_authz_context() -> None:
    """Reset the ContextVars to their unauthenticated defaults."""
    current_user_id.set(None)
    current_company_id.set(None)
    current_is_system_admin.set(False)
