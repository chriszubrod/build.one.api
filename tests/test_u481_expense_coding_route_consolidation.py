"""U-481 — Expense-coding route consolidation (U-477 Phase 2, API half).

Source / route-table pins — no live DB. Canonical `/expense/coding/*` spellings
are added; legacy `/expense-coding/*` paths stay as aliases on the SAME handler
so the two cannot drift. `claim` / `release` deliberately keep one spelling each.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.params import Depends as DependsParam

from shared.rbac_constants import Modules

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPENSE_ROUTER_PATH = REPO_ROOT / "entities/expense/api/router.py"

# (method, alias_path, canonical_path)
ALIASED_ROUTE_PAIRS: list[tuple[str, str, str]] = [
    ("GET", "/api/v1/expense-coding/queue", "/api/v1/get/expense/coding/queue"),
    ("POST", "/api/v1/expense-coding/suggest", "/api/v1/expense/coding/suggest"),
    ("GET", "/api/v1/expense-coding/metrics", "/api/v1/get/expense/coding/metrics"),
    (
        "POST",
        "/api/v1/expense-coding/{public_id}/flag",
        "/api/v1/expense/coding/{public_id}/flag",
    ),
    (
        "POST",
        "/api/v1/expense-coding/{public_id}/confirm",
        "/api/v1/expense/coding/{public_id}/confirm",
    ),
]

SINGLE_SPELLING_ONLY: list[tuple[str, str]] = [
    ("POST", "/api/v1/expense-coding/{public_id}/claim"),
    ("POST", "/api/v1/expense-coding/{public_id}/release"),
]

NEW_EXPENSE_CODING_READ = ("GET", "/api/v1/get/expense/{public_id}/coding")


def _app_routes():
    from app import app

    return list(app.routes)


def _route_paths(method: str | None = None) -> set[str]:
    paths: set[str] = set()
    for route in _app_routes():
        path = getattr(route, "path", None)
        if path is None:
            continue
        if method is not None:
            methods = getattr(route, "methods", None) or set()
            if method not in methods:
                continue
        paths.add(path)
    return paths


def _endpoint_for(method: str, path: str):
    for route in _app_routes():
        if getattr(route, "path", None) != path:
            continue
        methods = getattr(route, "methods", None) or set()
        if method in methods:
            return route.endpoint
    return None


def _route_for(method: str, path: str):
    for route in _app_routes():
        if getattr(route, "path", None) != path:
            continue
        methods = getattr(route, "methods", None) or set()
        if method in methods:
            return route
    return None


def _route_index(path: str) -> int:
    for index, route in enumerate(_app_routes()):
        if getattr(route, "path", None) == path:
            return index
    raise AssertionError(f"path not registered: {path}")


# ---------------------------------------------------------------------------
# 1 — both spellings registered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,alias_path,canonical_path", ALIASED_ROUTE_PAIRS)
def test_both_spellings_registered(method, alias_path, canonical_path):
    paths = _route_paths(method)
    assert alias_path in paths, f"alias missing: {method} {alias_path}"
    assert canonical_path in paths, f"canonical missing: {method} {canonical_path}"


# ---------------------------------------------------------------------------
# 2 — same handler object (no duplicated bodies)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,alias_path,canonical_path", ALIASED_ROUTE_PAIRS)
def test_alias_and_canonical_share_handler_object(method, alias_path, canonical_path):
    alias_endpoint = _endpoint_for(method, alias_path)
    canonical_endpoint = _endpoint_for(method, canonical_path)
    assert alias_endpoint is not None, f"no alias route: {method} {alias_path}"
    assert canonical_endpoint is not None, f"no canonical route: {method} {canonical_path}"
    assert alias_endpoint is canonical_endpoint, (
        f"{method} {alias_path} and {canonical_path} must share one handler; "
        f"got {alias_endpoint!r} vs {canonical_endpoint!r}"
    )


# ---------------------------------------------------------------------------
# 3 — literal coding segments before /get/expense/{public_id}
# ---------------------------------------------------------------------------


def test_literal_coding_queue_and_metrics_routes_precede_expense_public_id():
    expense_by_id = "/api/v1/get/expense/{public_id}"
    queue = "/api/v1/get/expense/coding/queue"
    metrics = "/api/v1/get/expense/coding/metrics"
    idx_expense = _route_index(expense_by_id)
    idx_queue = _route_index(queue)
    idx_metrics = _route_index(metrics)
    assert idx_queue < idx_expense, (
        f"{queue} (index {idx_queue}) must register before "
        f"{expense_by_id} (index {idx_expense}) or 'coding' is swallowed as public_id"
    )
    assert idx_metrics < idx_expense, (
        f"{metrics} (index {idx_metrics}) must register before "
        f"{expense_by_id} (index {idx_expense}) or 'coding' is swallowed as public_id"
    )


# ---------------------------------------------------------------------------
# 4 — deprecated flag on aliases only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,alias_path,canonical_path", ALIASED_ROUTE_PAIRS)
def test_alias_routes_marked_deprecated(method, alias_path, canonical_path):
    alias_route = _route_for(method, alias_path)
    canonical_route = _route_for(method, canonical_path)
    assert alias_route is not None
    assert canonical_route is not None
    assert getattr(alias_route, "deprecated", False) is True, (
        f"alias {alias_path} must carry deprecated=True"
    )
    assert not getattr(canonical_route, "deprecated", False), (
        f"canonical {canonical_path} must not be deprecated"
    )


# ---------------------------------------------------------------------------
# 5 — claim / release stay single-spelling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", SINGLE_SPELLING_ONLY)
def test_claim_and_release_routes_are_gone_in_both_spellings(method, path):
    # Chris, 2026-09-18: drop the two unused HTTP routes, keep the service lease
    # (confirm() auto-claims via ExpenseCodingItemService.claim).
    paths = _route_paths(method)
    assert path not in paths, f"legacy route still registered: {method} {path}"
    alt = path.replace("/expense-coding/", "/expense/coding/")
    assert alt not in paths, f"canonical spelling still registered: {method} {alt}"


def test_claim_and_release_routes_are_not_registered_at_all():
    # Chris, 2026-09-18: drop the two unused HTTP routes, keep the service lease.
    paths = _route_paths("POST")
    claim_paths = [p for p in paths if p.endswith("/claim") and "coding" in p]
    release_paths = [p for p in paths if p.endswith("/release") and "coding" in p]
    assert claim_paths == []
    assert release_paths == []


# ---------------------------------------------------------------------------
# 6 — new single-expense coding read
# ---------------------------------------------------------------------------


def test_single_expense_coding_route_registered():
    method, path = NEW_EXPENSE_CODING_READ
    assert _endpoint_for(method, path) is not None


def test_single_expense_coding_route_gated_on_expenses_can_read():
    method, path = NEW_EXPENSE_CODING_READ
    endpoint = _endpoint_for(method, path)
    assert endpoint is not None
    sig = inspect.signature(endpoint)
    rbac_param = None
    for name in ("_", "current_user"):
        if name in sig.parameters:
            rbac_param = sig.parameters[name]
            break
    assert rbac_param is not None, "route must declare RBAC Depends"
    dep = rbac_param.default
    assert isinstance(dep, DependsParam)
    inner = dep.dependency
    assert inner.__qualname__ == "require_module_api.<locals>._dependency"
    nonlocals = inspect.getclosurevars(inner).nonlocals
    assert nonlocals.get("module_name") == Modules.EXPENSES
    assert nonlocals.get("permission") == "can_read"


def test_single_expense_coding_handler_reuses_build_expense_coding_block():
    """Must not re-implement the coding block — reuse Phase-1 helper."""
    src = EXPENSE_ROUTER_PATH.read_text()
    assert "def get_expense_public_id_coding_router" in src
    endpoint = _endpoint_for(*NEW_EXPENSE_CODING_READ)
    assert endpoint is not None
    handler_src = inspect.getsource(endpoint)
    assert "build_expense_coding_block" in handler_src, (
        "handler must call build_expense_coding_block, not re-implement the block"
    )
    assert "read_state_by_expense_ids" in handler_src
