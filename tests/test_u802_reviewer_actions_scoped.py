"""U-802 — reviewer actions (approve / reject / review-flag) are actor-scoped.

Before U-802, `approve` and `reject` read the entry with `actor_is_system_admin=True`,
so any Time Tracking can_approve holder could act on ANY entry. `stamp_review` had no
scoped read at all, and the `/review-flag` route was gated on can_update.

After U-802 the reads are scoped to the actor (own rows, plus teammates' rows on
shared projects via CanViewTeam). System admins and system context still bypass.
No database is opened: repos are MagicMocks and the module-level repo classes are
patched.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import inspect

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import entities.time_entry.api.router as router_mod
import entities.time_entry.business.service as svc_mod
from entities.auth.business.service import get_current_user_api
from shared.authz.context import (
    clear_authz_context,
    current_can_view_team_modules,
    set_authz_context,
)

import shared.rbac as rbac

PM_ID = 10  # reviewer with can_approve + CanViewTeam on Time Tracking
ADMIN_ID = 1
OWNER_ID = 20
NOT_FOUND = "TimeEntry with public_id 'pub7' not found."


@pytest.fixture(autouse=True)
def _reset_ctx():
    clear_authz_context()
    current_can_view_team_modules.set(frozenset())
    yield
    clear_authz_context()
    current_can_view_team_modules.set(frozenset())


def _actor(user_id, *, admin=False, system=False, team=True):
    set_authz_context(
        user_id=user_id,
        company_id=1,
        is_system_admin=admin,
        is_system_context=system,
    )
    current_can_view_team_modules.set(frozenset({"Time Tracking"}) if team else frozenset())


def _entry():
    return SimpleNamespace(id=7, public_id="pub7", user_id=OWNER_ID, row_version="rv1")


@pytest.fixture
def mocks(monkeypatch):
    repo = MagicMock(name="TimeEntryRepository")
    repo.read_by_public_id.return_value = _entry()
    repo.is_downstream_locked.return_value = False
    repo.stamp_review.return_value = 1

    status_repo = MagicMock(name="TimeEntryStatusRepository")
    status_repo.read_current.return_value = SimpleNamespace(status="submitted")
    monkeypatch.setattr(svc_mod, "TimeEntryStatusRepository", MagicMock(return_value=status_repo))
    return SimpleNamespace(repo=repo, status=status_repo)


def _svc(m):
    return svc_mod.TimeEntryService(repo=m.repo)


def _scoped_kwargs(mock_call):
    kw = mock_call.kwargs
    return kw.get("actor_user_id"), kw.get("actor_is_system_admin"), kw.get("actor_can_view_team")


# ------------------------------------------------------------ approve / reject


@pytest.mark.parametrize("method", ["approve", "reject"])
def test_reviewer_read_is_scoped_to_actor_not_admin_bypass(mocks, method):
    _actor(PM_ID, team=True)
    getattr(_svc(mocks), method)("pub7", user_id=PM_ID)
    mocks.repo.read_by_public_id.assert_called_once()
    assert _scoped_kwargs(mocks.repo.read_by_public_id.call_args) == (PM_ID, False, True)


@pytest.mark.parametrize("method", ["approve", "reject"])
def test_reviewer_miss_raises_not_found_and_writes_nothing(mocks, method):
    mocks.repo.read_by_public_id.return_value = None
    _actor(PM_ID, team=True)
    with pytest.raises(ValueError) as exc:
        getattr(_svc(mocks), method)("pub7", user_id=PM_ID)
    assert str(exc.value) == NOT_FOUND
    mocks.status.create.assert_not_called()


def test_approve_hit_creates_approved_status_as_before(mocks):
    _actor(PM_ID, team=True)
    entry = _svc(mocks).approve("pub7", user_id=PM_ID, note="ok")
    assert entry.public_id == "pub7"
    mocks.status.create.assert_called_once_with(
        time_entry_id=7, status="approved", user_id=PM_ID, note="ok"
    )


def test_reject_hit_creates_rejected_then_draft_as_before(mocks):
    _actor(PM_ID, team=True)
    _svc(mocks).reject("pub7", user_id=PM_ID)
    statuses = [c.kwargs["status"] for c in mocks.status.create.call_args_list]
    assert statuses == ["rejected", "draft"]


@pytest.mark.parametrize("method", ["approve", "reject"])
def test_system_admin_read_is_admin_bypass_and_proceeds(mocks, method):
    _actor(ADMIN_ID, admin=True)
    getattr(_svc(mocks), method)("pub7", user_id=ADMIN_ID)
    assert mocks.repo.read_by_public_id.call_args.kwargs["actor_is_system_admin"] is True
    mocks.status.create.assert_called()


# ------------------------------------------------------------------ stamp_review


def test_stamp_review_out_of_scope_miss_raises_and_does_not_stamp(mocks):
    mocks.repo.read_by_public_id.return_value = None
    _actor(PM_ID, team=False)
    with pytest.raises(ValueError) as exc:
        _svc(mocks).stamp_review(public_id="pub7", priority="high", reasons=[])
    assert str(exc.value) == NOT_FOUND
    mocks.repo.stamp_review.assert_not_called()


def test_stamp_review_scoped_read_uses_actor(mocks):
    _actor(PM_ID, team=True)
    _svc(mocks).stamp_review(public_id="pub7", priority="high", reasons=[])
    assert _scoped_kwargs(mocks.repo.read_by_public_id.call_args) == (PM_ID, False, True)


def test_stamp_review_in_scope_stamps(mocks):
    _actor(PM_ID, team=True)
    out = _svc(mocks).stamp_review(public_id="pub7", priority="medium", reasons=[])
    mocks.repo.stamp_review.assert_called_once()
    assert out["affected_row_count"] == 1


def test_stamp_review_invalid_priority_still_validated_first(mocks):
    _actor(PM_ID, team=True)
    with pytest.raises(ValueError) as exc:
        _svc(mocks).stamp_review(public_id="pub7", priority="bogus", reasons=[])
    assert "Invalid ReviewPriority" in str(exc.value)
    mocks.repo.read_by_public_id.assert_not_called()
    mocks.repo.stamp_review.assert_not_called()


def test_stamp_review_system_context_as_auto_submit_sets_proceeds(mocks):
    # auto_submit_service sets is_system_admin=True + is_system_context=True before
    # calling stamp_review; the scoped read must bypass and the stamp must land.
    _actor(None, admin=True, system=True, team=False)
    out = _svc(mocks).stamp_review(public_id="pub7", priority="clean", reasons=[])
    assert mocks.repo.read_by_public_id.call_args.kwargs["actor_is_system_admin"] is True
    mocks.repo.stamp_review.assert_called_once()
    assert out["affected_row_count"] == 1


# ------------------------------------------------------------------------ route


def test_review_flag_route_requires_can_approve():
    route = next(
        r for r in router_mod.router.routes
        if getattr(r, "path", "").endswith("/review-flag") and "POST" in getattr(r, "methods", set())
    )
    param = inspect.signature(route.endpoint).parameters["current_user"]
    inner = param.default.dependency
    nonlocals = inspect.getclosurevars(inner).nonlocals
    assert nonlocals.get("module_name") == "Time Tracking"
    assert nonlocals.get("permission") == "can_approve"


_USERS = {}


async def _fake_get_current_user_api(request: Request):
    sub = request.headers.get("x-test-sub", "alice")
    uid = _USERS.get(sub, (None, None))[0]
    set_authz_context(user_id=uid, company_id=1, is_system_admin=False)
    return {"sub": sub, "user_id": uid, "company_id": 1, "is_system_admin": False}


def _perms(**flags):
    base = dict(can_read=True, can_create=False, can_update=False, can_delete=False,
                can_submit=False, can_approve=False, can_complete=False, can_view_team=False)
    base.update(flags)
    return {"Time Tracking": SimpleNamespace(**base)}


@pytest.fixture
def client(monkeypatch):
    _USERS.clear()
    rbac._permission_cache.clear()
    monkeypatch.setattr(rbac, "_resolve_permissions_from_db",
                        lambda *, user_id, company_id, is_system_admin: _USERS["alice"][1])
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[get_current_user_api] = _fake_get_current_user_api
    yield TestClient(app)
    rbac._permission_cache.clear()
    _USERS.clear()


def test_update_only_grant_is_403_on_review_flag(client, monkeypatch):
    _USERS["alice"] = (101, _perms(can_update=True, can_approve=False))
    monkeypatch.setattr(router_mod, "TimeEntryService", MagicMock(name="TimeEntryService"))
    r = client.post("/api/v1/time-entries/pub7/review-flag",
                    json={"priority": "high", "reasons": []},
                    headers={"x-test-sub": "alice"})
    assert r.status_code == 403


def test_approve_grant_reaches_review_flag_handler(client, monkeypatch):
    _USERS["alice"] = (101, _perms(can_approve=True))
    fake_svc = MagicMock(name="TimeEntryService")
    fake_svc.return_value.stamp_review.return_value = {
        "time_entry_public_id": "pub7", "priority": "high", "reasons": [],
        "affected_row_count": 1,
    }
    monkeypatch.setattr(router_mod, "TimeEntryService", fake_svc)
    r = client.post("/api/v1/time-entries/pub7/review-flag",
                    json={"priority": "high", "reasons": []},
                    headers={"x-test-sub": "alice"})
    assert r.status_code == 200, r.text
    fake_svc.return_value.stamp_review.assert_called_once()
