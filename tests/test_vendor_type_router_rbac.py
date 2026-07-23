"""Pure-logic guard: vendor-type routes must use Modules.VENDORS per-verb RBAC."""

from __future__ import annotations

import inspect

import pytest
from fastapi.params import Depends as DependsParam

from entities.vendor_type.api.router import router
from shared.rbac_constants import Modules

_EXPECTED_BY_ROUTE: list[tuple[str, str, str]] = [
    ("POST", "/api/v1/create/vendor-type", "can_create"),
    ("GET", "/api/v1/get/vendor-types", "can_read"),
    ("GET", "/api/v1/get/vendor-type/{public_id}", "can_read"),
    ("PUT", "/api/v1/update/vendor-type/{public_id}", "can_update"),
    ("DELETE", "/api/v1/delete/vendor-type/{public_id}", "can_delete"),
]


def _full_path(route_path: str) -> str:
    if route_path.startswith(router.prefix):
        return route_path
    prefix = router.prefix.rstrip("/")
    path = route_path if route_path.startswith("/") else f"/{route_path}"
    return f"{prefix}{path}"


def _endpoint_for(method: str, path: str):
    for route in router.routes:
        if getattr(route, "path", None) is None:
            continue
        if _full_path(route.path) != path:
            continue
        methods = getattr(route, "methods", None) or set()
        if method in methods:
            return route.endpoint
    return None


def _assert_current_user_rbac(endpoint, expected_permission: str) -> None:
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
    assert nonlocals.get("module_name") == Modules.VENDORS, nonlocals
    assert nonlocals.get("permission") == expected_permission, nonlocals


@pytest.mark.parametrize("method,path,permission", _EXPECTED_BY_ROUTE)
def test_vendor_type_route_uses_vendors_module_rbac(method: str, path: str, permission: str):
    endpoint = _endpoint_for(method, path)
    assert endpoint is not None, f"no route registered for {method} {path}"
    _assert_current_user_rbac(endpoint, permission)
