"""U-766 round 2 — teammates' time entries are read + approve/reject only.

CanViewTeam opens a teammate's TimeEntry for READ and for APPROVE/REJECT. Edit,
delete, submit and every TimeLog create/update/delete stay with the entry's own
worker. System admins and system context are exempt. No database is opened:
the repos are MagicMocks and the module-level repo classes are patched.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import entities.time_entry.business.service as svc_mod
import entities.time_entry.business.time_log_service as tls_mod
from shared.api import responses
from shared.api.responses import raise_workflow_error
from shared.authz.context import (
    clear_authz_context,
    current_can_view_team_modules,
    set_authz_context,
)

OWNER_ID = 20  # the entry's worker
TEAMMATE_ID = 10  # PM with CanViewTeam on Time Tracking
PREFIX = "Only this day's worker can change this entry: "
CODE = "entry_not_owner"


@pytest.fixture(autouse=True)
def _reset_ctx():
    clear_authz_context()
    yield
    clear_authz_context()


def _actor(user_id, *, admin=False, system=False, team=True):
    set_authz_context(
        user_id=user_id,
        company_id=1,
        is_system_admin=admin,
        is_system_context=system,
    )
    current_can_view_team_modules.set(frozenset({"Time Tracking"}) if team else frozenset())


def _entry(owner=OWNER_ID):
    return SimpleNamespace(
        id=7,
        public_id="pub7",
        user_id=owner,
        work_date="2026-11-02",
        note=None,
        row_version="rv1",
        time_entry_id=7,
    )


def _status(value="draft"):
    return SimpleNamespace(status=value)


# ---------------------------------------------------------------- TimeEntryService


@pytest.fixture
def entry_mocks(monkeypatch):
    repo = MagicMock(name="TimeEntryRepository")
    repo.read_by_public_id.return_value = _entry()
    repo.is_downstream_locked.return_value = False

    status_repo = MagicMock(name="TimeEntryStatusRepository")
    status_repo.read_current.return_value = _status("draft")
    monkeypatch.setattr(svc_mod, "TimeEntryStatusRepository", MagicMock(return_value=status_repo))

    log_repo = MagicMock(name="TimeLogRepository")
    log_repo.read_by_time_entry_id.return_value = [SimpleNamespace(id=1)]
    monkeypatch.setattr(svc_mod, "TimeLogRepository", MagicMock(return_value=log_repo))

    user_svc = MagicMock(name="UserService")
    user_svc.return_value.read_by_public_id.return_value = SimpleNamespace(id=99)
    monkeypatch.setattr(svc_mod, "UserService", user_svc)

    outbox = MagicMock(name="TimeTrackingOutboxService")
    import intelligence.outbox.business.service as outbox_mod

    monkeypatch.setattr(outbox_mod, "TimeTrackingOutboxService", outbox)

    return SimpleNamespace(repo=repo, status=status_repo, logs=log_repo, users=user_svc, outbox=outbox)


def _svc(m):
    return svc_mod.TimeEntryService(repo=m.repo)


def test_teammate_update_refused_and_not_written(entry_mocks):
    _actor(TEAMMATE_ID)
    with pytest.raises(ValueError) as exc:
        _svc(entry_mocks).update_by_public_id("pub7", row_version="rv1", note="x")
    assert str(exc.value).startswith(PREFIX)
    entry_mocks.repo.update_by_id.assert_not_called()


def test_teammate_update_cannot_reassign_owner(entry_mocks):
    entry = _entry()
    entry_mocks.repo.read_by_public_id.return_value = entry
    _actor(TEAMMATE_ID)
    with pytest.raises(ValueError) as exc:
        _svc(entry_mocks).update_by_public_id("pub7", row_version="rv1", user_public_id="pubU")
    assert str(exc.value).startswith(PREFIX)
    entry_mocks.repo.update_by_id.assert_not_called()
    entry_mocks.users.return_value.read_by_public_id.assert_not_called()
    assert entry.user_id == OWNER_ID


def test_teammate_delete_refused_and_not_written(entry_mocks):
    _actor(TEAMMATE_ID)
    with pytest.raises(ValueError) as exc:
        _svc(entry_mocks).delete_by_public_id("pub7", row_version="rv1")
    assert str(exc.value).startswith(PREFIX)
    entry_mocks.repo.delete_by_id.assert_not_called()


def test_teammate_submit_refused_no_status_agg_or_outbox(entry_mocks):
    _actor(TEAMMATE_ID)
    with pytest.raises(ValueError) as exc:
        _svc(entry_mocks).submit("pub7", user_id=TEAMMATE_ID)
    assert str(exc.value).startswith(PREFIX)
    entry_mocks.status.create.assert_not_called()
    entry_mocks.repo.aggregate_for_billing.assert_not_called()
    entry_mocks.outbox.assert_not_called()


@pytest.mark.parametrize("method", ["approve", "reject"])
def test_teammate_approve_reject_not_refused_by_guard(entry_mocks, method):
    _actor(TEAMMATE_ID)
    try:
        getattr(_svc(entry_mocks), method)("pub7", user_id=TEAMMATE_ID)
    except ValueError as exc:
        assert not str(exc).startswith(PREFIX), f"{method} must not be owner-gated"
    entry_mocks.repo.read_by_public_id.assert_called()


def test_owner_update_delete_submit_proceed(entry_mocks):
    _actor(OWNER_ID)
    svc = _svc(entry_mocks)
    svc.update_by_public_id("pub7", row_version="rv1", note="mine")
    entry_mocks.repo.update_by_id.assert_called_once()

    svc.delete_by_public_id("pub7", row_version="rv1")
    entry_mocks.repo.delete_by_id.assert_called_once()

    svc.submit("pub7", user_id=OWNER_ID)
    entry_mocks.status.create.assert_called_once()
    entry_mocks.outbox.assert_called_once()


def test_admin_exempt_from_owner_gate(entry_mocks):
    _actor(1, admin=True)
    _svc(entry_mocks).update_by_public_id("pub7", row_version="rv1", note="admin")
    entry_mocks.repo.update_by_id.assert_called_once()


def test_system_context_exempt_from_owner_gate(entry_mocks):
    _actor(TEAMMATE_ID, system=True, team=False)
    _svc(entry_mocks).delete_by_public_id("pub7", row_version="rv1")
    entry_mocks.repo.delete_by_id.assert_called_once()


def test_actor_none_refused(entry_mocks):
    _actor(None)
    with pytest.raises(ValueError) as exc:
        _svc(entry_mocks).update_by_public_id("pub7", row_version="rv1", note="x")
    assert str(exc.value).startswith(PREFIX)
    entry_mocks.repo.update_by_id.assert_not_called()


# ---------------------------------------------------------------- TimeLogService


@pytest.fixture
def log_mocks(monkeypatch):
    entry_repo = MagicMock(name="TimeEntryRepository")
    entry_repo.read_by_public_id.return_value = _entry()
    entry_repo.read_by_id.return_value = _entry()
    monkeypatch.setattr(tls_mod, "TimeEntryRepository", MagicMock(return_value=entry_repo))

    status_repo = MagicMock(name="TimeEntryStatusRepository")
    status_repo.read_current.return_value = _status("draft")
    monkeypatch.setattr(tls_mod, "TimeEntryStatusRepository", MagicMock(return_value=status_repo))

    log_repo = MagicMock(name="TimeLogRepository")
    log_repo.read_by_public_id.return_value = SimpleNamespace(
        id=3,
        time_entry_id=7,
        clock_in="2026-11-02 08:00:00",
        clock_out=None,
        log_type="work",
        duration=None,
        row_version="rv1",
    )
    return SimpleNamespace(entries=entry_repo, status=status_repo, logs=log_repo)


def _tls(m):
    return tls_mod.TimeLogService(repo=m.logs)


def test_teammate_log_create_refused(log_mocks):
    _actor(TEAMMATE_ID)
    with pytest.raises(ValueError) as exc:
        _tls(log_mocks).create(time_entry_public_id="pub7", clock_in="2026-11-02 08:00:00")
    assert str(exc.value).startswith(PREFIX)
    log_mocks.logs.create.assert_not_called()


def test_teammate_log_update_refused(log_mocks):
    _actor(TEAMMATE_ID)
    with pytest.raises(ValueError) as exc:
        _tls(log_mocks).update_by_public_id("log3", row_version="rv1", note="x")
    assert str(exc.value).startswith(PREFIX)
    log_mocks.logs.update_by_id.assert_not_called()


def test_teammate_log_delete_refused(log_mocks):
    _actor(TEAMMATE_ID)
    with pytest.raises(ValueError) as exc:
        _tls(log_mocks).delete_by_public_id("log3")
    assert str(exc.value).startswith(PREFIX)
    log_mocks.logs.delete_by_id.assert_not_called()


def test_owner_log_writes_proceed(log_mocks):
    _actor(OWNER_ID)
    svc = _tls(log_mocks)
    svc.create(time_entry_public_id="pub7", clock_in="2026-11-02 08:00:00")
    log_mocks.logs.create.assert_called_once()

    svc.update_by_public_id("log3", row_version="rv1", note="mine")
    log_mocks.logs.update_by_id.assert_called_once()

    svc.delete_by_public_id("log3")
    log_mocks.logs.delete_by_id.assert_called_once()


def test_admin_log_writes_exempt(log_mocks):
    _actor(1, admin=True)
    _tls(log_mocks).delete_by_public_id("log3")
    log_mocks.logs.delete_by_id.assert_called_once()


# ---------------------------------------------------------------- error mapping


def test_prefix_constant_is_the_u766_prefix():
    assert getattr(responses, "ENTRY_NOT_OWNER_PREFIX", None) == PREFIX


def test_prefix_maps_to_403_entry_not_owner():
    with pytest.raises(HTTPException) as exc:
        raise_workflow_error(PREFIX + "x", "d")
    assert exc.value.status_code == 403
    assert getattr(exc.value, "error_code", None) == CODE


# ---------------------------------------------------------------- reassignment (fix round 2)


@pytest.mark.parametrize("target", [SimpleNamespace(id=99), None], ids=["other_worker", "unknown_user"])
def test_owner_cannot_move_their_day_to_another_worker(entry_mocks, target):
    # Same refusal for a real other worker and an unknown id: no user-existence oracle.
    entry = _entry()
    entry_mocks.repo.read_by_public_id.return_value = entry
    entry_mocks.users.return_value.read_by_public_id.return_value = target
    _actor(OWNER_ID, team=False)
    with pytest.raises(ValueError) as exc:
        _svc(entry_mocks).update_by_public_id("pub7", row_version="rv1", user_public_id="pubX")
    assert str(exc.value).startswith(PREFIX)
    entry_mocks.repo.update_by_id.assert_not_called()
    assert entry.user_id == OWNER_ID


def test_owner_resending_own_id_still_saves(entry_mocks):
    # The web edit page sends user_public_id = the day's worker on every PUT.
    entry_mocks.users.return_value.read_by_public_id.return_value = SimpleNamespace(id=OWNER_ID)
    _actor(OWNER_ID, team=False)
    _svc(entry_mocks).update_by_public_id("pub7", row_version="rv1", user_public_id="pubOwner", note="n")
    entry_mocks.repo.update_by_id.assert_called_once()


@pytest.mark.parametrize("admin,system", [(True, False), (False, True)], ids=["system_admin", "system_context"])
def test_admin_or_system_can_move_a_day(entry_mocks, admin, system):
    entry = _entry()
    entry_mocks.repo.read_by_public_id.return_value = entry
    entry_mocks.users.return_value.read_by_public_id.return_value = SimpleNamespace(id=99)
    _actor(1, admin=admin, system=system, team=False)
    _svc(entry_mocks).update_by_public_id("pub7", row_version="rv1", user_public_id="pub99")
    entry_mocks.repo.update_by_id.assert_called_once()
    assert entry.user_id == 99


def test_admin_unknown_user_keeps_not_found_message(entry_mocks):
    entry_mocks.users.return_value.read_by_public_id.return_value = None
    _actor(1, admin=True, team=False)
    with pytest.raises(ValueError) as exc:
        _svc(entry_mocks).update_by_public_id("pub7", row_version="rv1", user_public_id="nope")
    assert "not found" in str(exc.value)
