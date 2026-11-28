"""U-766 — can_view_team must reach the route handler through require_module_api.

Bug: `require_module_api` returned a sync `def _dependency`. FastAPI runs sync
dependencies in a threadpool with a COPY of the request context, so the
`current_can_view_team_modules` ContextVar that `_get_user_permissions` publishes
was discarded before the route handler read it. `get_current_user_api` is async
and resets the var, so handlers always saw an empty set.

These tests run the REAL `require_module_api("Time Tracking")` on a tiny app.
Only the DB resolver (`_resolve_permissions_from_db`) and the auth dependency
are replaced. No database connection is opened.
"""

import inspect
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

import shared.rbac as rbac
from shared.authz import current_can_view_team
from shared.authz.context import set_authz_context
from entities.auth.business.service import get_current_user_api

MODULE = "Time Tracking"

# sub -> (user_id, perms-or-None). Filled per test.
USERS: dict = {}
RESOLVER_CALLS: list = []
RAN: list = []


def _fake_resolver(*, user_id, company_id, is_system_admin):
    RESOLVER_CALLS.append(user_id)
    if is_system_admin:
        return rbac.SYSTEM_ADMIN_GRANT
    for _sub, (uid, perms) in USERS.items():
        if uid == user_id:
            return perms
    return None


async def _fake_get_current_user_api(request: Request):
    sub = request.headers.get("x-test-sub", "alice")
    is_admin = request.headers.get("x-test-admin") == "1"
    uid = USERS.get(sub, (None, None))[0]
    set_authz_context(
        user_id=uid,
        company_id=1,
        is_system_admin=is_admin,
    )
    return {
        "sub": sub,
        "user_id": uid,
        "company_id": 1,
        "is_system_admin": is_admin,
    }


def _perms(*, can_view_team: bool, can_create: bool = False):
    return {
        MODULE: SimpleNamespace(
            can_read=True,
            can_create=can_create,
            can_view_team=can_view_team,
        )
    }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    rbac._permission_cache.clear()
    USERS.clear()
    RESOLVER_CALLS.clear()
    RAN.clear()
    monkeypatch.setattr(rbac, "_resolve_permissions_from_db", _fake_resolver)
    yield
    rbac._permission_cache.clear()
    USERS.clear()


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/async-read")
    async def async_read(_u=Depends(rbac.require_module_api(MODULE))):
        return {"view_team": current_can_view_team(MODULE)}

    @app.get("/sync-read")
    def sync_read(_u=Depends(rbac.require_module_api(MODULE))):
        return {"view_team": current_can_view_team(MODULE)}

    @app.post("/create-gated")
    def create_gated(_u=Depends(rbac.require_module_api(MODULE, "can_create"))):
        RAN.append(True)
        return {"ok": True}

    app.dependency_overrides[get_current_user_api] = _fake_get_current_user_api
    return app


@pytest.fixture
def client():
    return TestClient(_build_app())


def test_grant_reaches_async_handler(client):
    USERS["alice"] = (101, _perms(can_view_team=True))
    r = client.get("/async-read", headers={"x-test-sub": "alice"})
    assert r.status_code == 200
    assert r.json() == {"view_team": True}


def test_grant_reaches_sync_handler(client):
    USERS["alice"] = (101, _perms(can_view_team=True))
    r = client.get("/sync-read", headers={"x-test-sub": "alice"})
    assert r.status_code == 200
    assert r.json() == {"view_team": True}


def test_no_grant_reads_false(client):
    USERS["alice"] = (101, _perms(can_view_team=False))
    r = client.get("/sync-read", headers={"x-test-sub": "alice"})
    assert r.status_code == 200
    assert r.json() == {"view_team": False}


def test_cache_hit_still_reads_true(client):
    USERS["alice"] = (101, _perms(can_view_team=True))
    first = client.get("/sync-read", headers={"x-test-sub": "alice"})
    second = client.get("/sync-read", headers={"x-test-sub": "alice"})
    assert first.json() == {"view_team": True}
    assert second.json() == {"view_team": True}
    assert RESOLVER_CALLS.count(101) == 1, "second request must be served from cache"


def test_no_leak_between_users(client):
    USERS["alice"] = (101, _perms(can_view_team=True))
    USERS["bob"] = (202, _perms(can_view_team=False))
    a = client.get("/async-read", headers={"x-test-sub": "alice"})
    b = client.get("/async-read", headers={"x-test-sub": "bob"})
    assert a.json() == {"view_team": True}
    assert b.json() == {"view_team": False}


def test_missing_permission_is_403_and_handler_not_run(client):
    USERS["alice"] = (101, _perms(can_view_team=True, can_create=False))
    r = client.post("/create-gated", headers={"x-test-sub": "alice"})
    assert r.status_code == 403
    assert RAN == []


def test_granted_permission_runs_handler(client):
    USERS["alice"] = (101, _perms(can_view_team=False, can_create=True))
    r = client.post("/create-gated", headers={"x-test-sub": "alice"})
    assert r.status_code == 200
    assert RAN == [True]


def test_system_admin_is_allowed(client):
    r = client.get("/sync-read", headers={"x-test-sub": "root", "x-test-admin": "1"})
    assert r.status_code == 200


def test_dependency_is_a_coroutine_function():
    # Pins the fix: the dependency must run on the event loop so the
    # ContextVar it publishes lands in the request's own context.
    assert inspect.iscoroutinefunction(rbac.require_module_api(MODULE))
