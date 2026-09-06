"""Pure-logic guard: the review-workflow route family (submit/advance/decline)
must gate on the dedicated `can_submit` permission, not `can_update`.

Migrated 2026-09-06 per docs/design/rolemodule-can_submit-audit.md: `can_update`
and `can_submit` prod RoleModule grants were audited and backfilled to match
before this migration, so the swap is a no-behavior-change permission split.
This test pins the swap so a future edit can't silently drift a route back onto
`can_update` (or onto the wrong module).
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.params import Depends as DependsParam

from entities.review.api.router import router as review_router
from entities.time_entry.api.router import router as time_entry_router
from shared.rbac_constants import Modules

# (router, method, path, module, permission)
_EXPECTED_BY_ROUTE: list[tuple[object, str, str, str, str]] = [
    # Bill review
    (review_router, "POST", "/api/v1/submit/review/bill/{public_id}", Modules.BILLS, "can_submit"),
    (review_router, "POST", "/api/v1/advance/review/bill/{public_id}", Modules.BILLS, "can_submit"),
    (review_router, "POST", "/api/v1/decline/review/bill/{public_id}", Modules.BILLS, "can_submit"),
    (review_router, "GET", "/api/v1/get/reviews/bill/{public_id}", Modules.BILLS, "can_read"),
    # Expense review
    (review_router, "POST", "/api/v1/submit/review/expense/{public_id}", Modules.EXPENSES, "can_submit"),
    (review_router, "POST", "/api/v1/advance/review/expense/{public_id}", Modules.EXPENSES, "can_submit"),
    (review_router, "POST", "/api/v1/decline/review/expense/{public_id}", Modules.EXPENSES, "can_submit"),
    (review_router, "GET", "/api/v1/get/reviews/expense/{public_id}", Modules.EXPENSES, "can_read"),
    # Bill Credit review
    (review_router, "POST", "/api/v1/submit/review/bill-credit/{public_id}", Modules.BILL_CREDITS, "can_submit"),
    (review_router, "POST", "/api/v1/advance/review/bill-credit/{public_id}", Modules.BILL_CREDITS, "can_submit"),
    (review_router, "POST", "/api/v1/decline/review/bill-credit/{public_id}", Modules.BILL_CREDITS, "can_submit"),
    (review_router, "GET", "/api/v1/get/reviews/bill-credit/{public_id}", Modules.BILL_CREDITS, "can_read"),
    # Invoice review
    (review_router, "POST", "/api/v1/submit/review/invoice/{public_id}", Modules.INVOICES, "can_submit"),
    (review_router, "POST", "/api/v1/advance/review/invoice/{public_id}", Modules.INVOICES, "can_submit"),
    (review_router, "POST", "/api/v1/decline/review/invoice/{public_id}", Modules.INVOICES, "can_submit"),
    (review_router, "GET", "/api/v1/get/reviews/invoice/{public_id}", Modules.INVOICES, "can_read"),
    # Contract Labor review — gated on Modules.TIME_TRACKING, not Modules.CONTRACT_LABOR
    # (the role that handles TimeTracking-sourced ContractLabor rows).
    (review_router, "POST", "/api/v1/submit/review/contract-labor/{public_id}", Modules.TIME_TRACKING, "can_submit"),
    (review_router, "POST", "/api/v1/advance/review/contract-labor/{public_id}", Modules.TIME_TRACKING, "can_submit"),
    (review_router, "POST", "/api/v1/decline/review/contract-labor/{public_id}", Modules.TIME_TRACKING, "can_submit"),
    (review_router, "GET", "/api/v1/get/reviews/contract-labor/{public_id}", Modules.TIME_TRACKING, "can_read"),
    # TimeEntry submit — router comment calls this "the same actor-gate" as the
    # contract-labor family above. Approve/reject stay on can_approve (untouched).
    (time_entry_router, "POST", "/api/v1/time-entries/{public_id}/submit", Modules.TIME_TRACKING, "can_submit"),
]


def _full_path(router, route_path: str) -> str:
    if route_path.startswith(router.prefix):
        return route_path
    prefix = router.prefix.rstrip("/")
    path = route_path if route_path.startswith("/") else f"/{route_path}"
    return f"{prefix}{path}"


def _endpoint_for(router, method: str, path: str):
    for route in router.routes:
        if getattr(route, "path", None) is None:
            continue
        if _full_path(router, route.path) != path:
            continue
        methods = getattr(route, "methods", None) or set()
        if method in methods:
            return route.endpoint
    return None


def _assert_current_user_rbac(endpoint, expected_module: str, expected_permission: str) -> None:
    sig = inspect.signature(endpoint)
    assert "current_user" in sig.parameters, "route must declare current_user"
    param = sig.parameters["current_user"]
    assert param.default is not inspect.Parameter.empty, "current_user must use Depends(...)"
    dep = param.default
    assert isinstance(dep, DependsParam), "current_user must be a FastAPI Depends"
    inner = dep.dependency
    assert inner.__qualname__ == "require_module_api.<locals>._dependency", (
        f"expected require_module_api RBAC, got {inner!r} ({inner.__qualname__})"
    )
    nonlocals = inspect.getclosurevars(inner).nonlocals
    assert nonlocals.get("module_name") == expected_module, nonlocals
    assert nonlocals.get("permission") == expected_permission, nonlocals


@pytest.mark.parametrize("router,method,path,module,permission", _EXPECTED_BY_ROUTE)
def test_review_route_uses_expected_module_and_permission(router, method, path, module, permission):
    endpoint = _endpoint_for(router, method, path)
    assert endpoint is not None, f"no route registered for {method} {path}"
    _assert_current_user_rbac(endpoint, module, permission)
