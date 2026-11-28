"""U-803 — GET /{public_id}/validate-completeness reads through the caller's scope.

Before U-803 the route read the entry and its logs with `actor_is_system_admin=True`,
so any Time Tracking can_read holder could read any worker's entry and logs. After
U-803 both reads are scoped to the actor (own rows, plus teammates' rows on shared
projects via CanViewTeam; system admins bypass). Out-of-scope is a 404.
No database is opened: repos are MagicMocks and the module-level classes are patched.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import entities.time_entry.api.router as router_mod
import entities.time_entry.business.service as svc_mod
import entities.time_entry.business.validation as validation_mod
import entities.time_entry.persistence.repo as repo_mod
import entities.time_entry.persistence.time_log_repo as log_repo_mod
from entities.auth.business.service import get_current_user_api
from shared.authz.context import (
    clear_authz_context,
    current_can_view_team_modules,
    set_authz_context,
)

import shared.rbac as rbac

ACTOR_ID = 10
ADMIN_ID = 1
OWNER_ID = 20
SENTINEL = "REPORT-803"
_ACTOR = {}


def _entry():
    return SimpleNamespace(id=7, public_id="pub7", user_id=OWNER_ID, row_version="rv1")


@pytest.fixture(autouse=True)
def _reset_ctx():
    clear_authz_context()
    current_can_view_team_modules.set(frozenset())
    yield
    clear_authz_context()
    current_can_view_team_modules.set(frozenset())


@pytest.fixture
def mocks(monkeypatch):
    entry_repo = MagicMock(name="TimeEntryRepository")
    entry_repo.read_by_public_id.return_value = _entry()
    log_repo = MagicMock(name="TimeLogRepository")
    log_repo.read_by_time_entry_id.return_value = [SimpleNamespace(id=1, time_entry_id=7)]
    validate = MagicMock(name="validate_completeness", return_value={"sentinel": SENTINEL})

    entry_cls = MagicMock(name="TimeEntryRepository_cls", return_value=entry_repo)
    # TimeEntryService binds TimeEntryRepository at construction; the route may import it too.
    monkeypatch.setattr(svc_mod, "TimeEntryRepository", entry_cls)
    monkeypatch.setattr(repo_mod, "TimeEntryRepository", entry_cls)
    monkeypatch.setattr(log_repo_mod, "TimeLogRepository", MagicMock(return_value=log_repo))
    monkeypatch.setattr(validation_mod, "validate_completeness", validate)
    return SimpleNamespace(entry=entry_repo, log=log_repo, validate=validate)


def _actor(user_id, *, admin=False, team=True):
    set_authz_context(user_id=user_id, company_id=1, is_system_admin=admin, is_system_context=False)
    current_can_view_team_modules.set(frozenset({"Time Tracking"}) if team else frozenset())


def _perms(**flags):
    base = dict(can_read=True, can_create=False, can_update=False, can_delete=False,
                can_submit=False, can_approve=False, can_complete=False, can_view_team=False)
    base.update(flags)
    return {"Time Tracking": SimpleNamespace(**base)}


async def _fake_get_current_user_api(request: Request):
    _actor(_ACTOR["user_id"], admin=_ACTOR["admin"], team=_ACTOR["team"])
    return {"sub": "alice", "user_id": _ACTOR["user_id"], "company_id": 1,
            "is_system_admin": _ACTOR["admin"]}


@pytest.fixture
def client(monkeypatch):
    _ACTOR.clear()
    rbac._permission_cache.clear()
    monkeypatch.setattr(rbac, "_resolve_permissions_from_db",
                        lambda *, user_id, company_id, is_system_admin: _perms(can_view_team=_ACTOR.get("team", False)))
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[get_current_user_api] = _fake_get_current_user_api
    yield TestClient(app)
    rbac._permission_cache.clear()
    _ACTOR.clear()


def _get(client):
    return client.get("/api/v1/time-entries/pub7/validate-completeness")


def _scoped(mock_call):
    kw = mock_call.kwargs
    return kw.get("actor_user_id"), kw.get("actor_is_system_admin"), kw.get("actor_can_view_team")


# ------------------------------------------------------------------ scoping


def test_non_admin_entry_and_log_reads_are_scoped_not_admin_bypass(client, mocks):
    _ACTOR.update(user_id=ACTOR_ID, admin=False, team=True)
    r = _get(client)
    assert r.status_code == 200, r.text
    mocks.entry.read_by_public_id.assert_called_once()
    assert _scoped(mocks.entry.read_by_public_id.call_args) == (ACTOR_ID, False, True)
    mocks.log.read_by_time_entry_id.assert_called_once()
    assert _scoped(mocks.log.read_by_time_entry_id.call_args) == (ACTOR_ID, False, True)


def test_non_admin_without_team_visibility_passes_false_can_view_team(client, mocks):
    _ACTOR.update(user_id=ACTOR_ID, admin=False, team=False)
    _get(client)
    assert _scoped(mocks.entry.read_by_public_id.call_args) == (ACTOR_ID, False, False)
    assert _scoped(mocks.log.read_by_time_entry_id.call_args) == (ACTOR_ID, False, False)


def test_system_admin_actor_passes_admin_flag_to_both_reads(client, mocks):
    _ACTOR.update(user_id=ADMIN_ID, admin=True, team=False)
    r = _get(client)
    assert r.status_code == 200, r.text
    assert mocks.entry.read_by_public_id.call_args.kwargs["actor_is_system_admin"] is True
    assert mocks.log.read_by_time_entry_id.call_args.kwargs["actor_is_system_admin"] is True


# ------------------------------------------------------------ out of scope


def test_out_of_scope_entry_is_404_and_logs_and_report_are_not_read(client, mocks):
    mocks.entry.read_by_public_id.return_value = None
    _ACTOR.update(user_id=ACTOR_ID, admin=False, team=False)
    r = _get(client)
    assert r.status_code == 404, r.text
    mocks.log.read_by_time_entry_id.assert_not_called()
    mocks.validate.assert_not_called()


# -------------------------------------------------------------- in scope


def test_in_scope_returns_report_from_validate_completeness(client, mocks):
    _ACTOR.update(user_id=ACTOR_ID, admin=False, team=True)
    r = _get(client)
    assert r.status_code == 200, r.text
    assert SENTINEL in r.text
    mocks.validate.assert_called_once()
    kw = mocks.validate.call_args.kwargs
    assert kw["entry"] is mocks.entry.read_by_public_id.return_value
    assert kw["logs"] is mocks.log.read_by_time_entry_id.return_value
