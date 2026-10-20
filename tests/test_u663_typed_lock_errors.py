"""U-663 — typed lock errors for time-tracking edits (entry_approved / entry_locked)."""

import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.time_entry.api.router import (
    create_time_log,
    delete_time_log,
    time_log_router,
    update_time_entry,
    update_time_log,
)
from entities.time_entry.business.time_log_service import TimeLogService
ENTRY_APPROVED_PREFIX = "This day's entry has been approved: "
ENTRY_LOCKED_PREFIX = "This day's entry is no longer a draft: "

LOG_PUBLIC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ENTRY_PUBLIC_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UPDATE_LOG_PATH = f"/api/v1/time-logs/{LOG_PUBLIC_ID}"
UPDATE_ENTRY_PATH = f"/api/v1/time-entries/{ENTRY_PUBLIC_ID}"
CREATE_LOG_PATH = f"/api/v1/time-entries/{ENTRY_PUBLIC_ID}/logs"
DELETE_LOG_PATH = f"/api/v1/time-logs/{LOG_PUBLIC_ID}"

_APPROVED_LOG_MSG = (
    f"{ENTRY_APPROVED_PREFIX}Cannot modify time logs when time entry is in 'approved' status. "
    "Only 'draft' entries can have their time logs modified."
)
_SUBMITTED_LOG_MSG = (
    f"{ENTRY_LOCKED_PREFIX}Cannot modify time logs when time entry is in 'submitted' status. "
    "Only 'draft' entries can have their time logs modified."
)
_APPROVED_ENTRY_MSG = (
    f"{ENTRY_APPROVED_PREFIX}Cannot update time entry in 'approved' status. "
    "Only 'draft' entries can be edited."
)
_SUBMITTED_ENTRY_MSG = (
    f"{ENTRY_LOCKED_PREFIX}Cannot update time entry in 'submitted' status. "
    "Only 'draft' entries can be edited."
)
_UNPREFIXED = "Some other business rule violation."


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def _override_rbac(route_fn):
    rbac_dependency = inspect.signature(route_fn).parameters["current_user"].default.dependency
    app.dependency_overrides[rbac_dependency] = lambda: {"sub": "test-sub", "user_id": 17}


def test_update_time_log_approved_returns_422_entry_approved(client, clear_dependency_overrides):
    _override_rbac(update_time_log)
    with patch.object(TimeLogService, "update_by_public_id", side_effect=ValueError(_APPROVED_LOG_MSG)):
        response = client.put(UPDATE_LOG_PATH, json={"row_version": "AAAAAAAAAAI="})
    assert response.status_code == 422
    assert response.json()["error_code"] == "entry_approved"


def test_update_time_log_submitted_returns_422_entry_locked(client, clear_dependency_overrides):
    _override_rbac(update_time_log)
    with patch.object(TimeLogService, "update_by_public_id", side_effect=ValueError(_SUBMITTED_LOG_MSG)):
        response = client.put(UPDATE_LOG_PATH, json={"row_version": "AAAAAAAAAAI="})
    assert response.status_code == 422
    assert response.json()["error_code"] == "entry_locked"


def test_create_time_log_on_approved_day_returns_entry_approved(client, clear_dependency_overrides):
    _override_rbac(create_time_log)
    with patch.object(TimeLogService, "create", side_effect=ValueError(_APPROVED_LOG_MSG)):
        response = client.post(
            CREATE_LOG_PATH,
            json={"clock_in": "2026-01-01 08:00:00", "log_type": "work"},
        )
    assert response.status_code == 422
    assert response.json()["error_code"] == "entry_approved"


def test_delete_time_log_on_approved_day_returns_entry_approved(client, clear_dependency_overrides):
    _override_rbac(delete_time_log)
    with patch.object(TimeLogService, "delete_by_public_id", side_effect=ValueError(_APPROVED_LOG_MSG)):
        response = client.delete(DELETE_LOG_PATH)
    assert response.status_code == 422
    assert response.json()["error_code"] == "entry_approved"


def test_update_time_entry_approved_returns_entry_approved(client, clear_dependency_overrides):
    _override_rbac(update_time_entry)
    with patch("entities.time_entry.api.router._resolve_user_id", return_value=17):
        with patch("entities.time_entry.api.router.ProcessEngine") as engine_cls:
            engine_cls.return_value.execute_synchronous.return_value = {
                "success": False,
                "error": _APPROVED_ENTRY_MSG,
            }
            response = client.put(
                UPDATE_ENTRY_PATH,
                json={"row_version": "AAAAAAAAAAI=", "work_date": "2026-01-01"},
            )
    assert response.status_code == 422
    assert response.json()["error_code"] == "entry_approved"


def test_update_time_entry_submitted_returns_entry_locked(client, clear_dependency_overrides):
    _override_rbac(update_time_entry)
    with patch("entities.time_entry.api.router._resolve_user_id", return_value=17):
        with patch("entities.time_entry.api.router.ProcessEngine") as engine_cls:
            engine_cls.return_value.execute_synchronous.return_value = {
                "success": False,
                "error": _SUBMITTED_ENTRY_MSG,
            }
            response = client.put(
                UPDATE_ENTRY_PATH,
                json={"row_version": "AAAAAAAAAAI=", "work_date": "2026-01-01"},
            )
    assert response.status_code == 422
    assert response.json()["error_code"] == "entry_locked"


def test_unprefixed_value_error_stays_400_without_code(client, clear_dependency_overrides):
    _override_rbac(update_time_log)
    with patch.object(TimeLogService, "update_by_public_id", side_effect=ValueError(_UNPREFIXED)):
        response = client.put(UPDATE_LOG_PATH, json={"row_version": "AAAAAAAAAAI="})
    assert response.status_code == 400
    assert response.json()["error_code"] is None


def test_validate_parent_is_draft_submitted_message_starts_with_entry_locked_prefix():
    from shared.api.responses import ENTRY_LOCKED_PREFIX as locked_prefix

    service = TimeLogService(repo=MagicMock())
    status_row = SimpleNamespace(status="submitted")
    with patch(
        "entities.time_entry.business.time_log_service.TimeEntryStatusRepository"
    ) as repo_cls:
        repo_cls.return_value.read_current.return_value = status_row
        with pytest.raises(ValueError) as excinfo:
            service._validate_parent_is_draft(time_entry_id=7)
    assert str(excinfo.value).startswith(locked_prefix)


def test_validate_parent_is_draft_approved_message_starts_with_entry_approved_prefix():
    from shared.api.responses import ENTRY_APPROVED_PREFIX as approved_prefix

    service = TimeLogService(repo=MagicMock())
    status_row = SimpleNamespace(status="approved")
    with patch(
        "entities.time_entry.business.time_log_service.TimeEntryStatusRepository"
    ) as repo_cls:
        repo_cls.return_value.read_current.return_value = status_row
        with pytest.raises(ValueError) as excinfo:
            service._validate_parent_is_draft(time_entry_id=7)
    assert str(excinfo.value).startswith(approved_prefix)


def _time_entry_service_with_status(entry_status):
    """A REAL TimeEntryService whose repo + status read are faked — so these tests
    go RED if the service drops the prefix, unlike the router tests that mock the
    service and raise the prefixed message themselves."""
    from entities.time_entry.business.service import TimeEntryService

    service = TimeEntryService(repo=MagicMock())
    service.repo.read_by_public_id.return_value = SimpleNamespace(id=7)
    status_patch = patch("entities.time_entry.business.service.TimeEntryStatusRepository")
    scope_patch = patch(
        "entities.time_entry.business.service._actor_scope", return_value=(17, False, True)
    )
    return service, status_patch, scope_patch, SimpleNamespace(status=entry_status)


@pytest.mark.parametrize(
    "entry_status, prefix_name",
    [("approved", "ENTRY_APPROVED_PREFIX"), ("submitted", "ENTRY_LOCKED_PREFIX")],
)
def test_time_entry_update_guard_message_starts_with_typed_prefix(entry_status, prefix_name):
    import shared.api.responses as responses

    service, status_patch, scope_patch, status_row = _time_entry_service_with_status(entry_status)
    with status_patch as repo_cls, scope_patch:
        repo_cls.return_value.read_current.return_value = status_row
        with pytest.raises(ValueError) as excinfo:
            service.update_by_public_id(public_id=ENTRY_PUBLIC_ID, row_version="AAAAAAAAAAI=")
    assert str(excinfo.value).startswith(getattr(responses, prefix_name))
    service.repo.update_by_id.assert_not_called()


@pytest.mark.parametrize(
    "entry_status, prefix_name",
    [("approved", "ENTRY_APPROVED_PREFIX"), ("submitted", "ENTRY_LOCKED_PREFIX")],
)
def test_time_entry_delete_guard_message_starts_with_typed_prefix(entry_status, prefix_name):
    import shared.api.responses as responses

    service, status_patch, scope_patch, status_row = _time_entry_service_with_status(entry_status)
    with status_patch as repo_cls, scope_patch:
        repo_cls.return_value.read_current.return_value = status_row
        with pytest.raises(ValueError) as excinfo:
            service.delete_by_public_id(public_id=ENTRY_PUBLIC_ID)
    assert str(excinfo.value).startswith(getattr(responses, prefix_name))
    service.repo.delete_by_id.assert_not_called()


def test_entry_lock_prefix_helper_distinguishes_approved_from_other_locks():
    from shared.api.responses import ENTRY_APPROVED_PREFIX, ENTRY_LOCKED_PREFIX, entry_lock_prefix

    assert entry_lock_prefix("approved") == ENTRY_APPROVED_PREFIX
    assert entry_lock_prefix("submitted") == ENTRY_LOCKED_PREFIX
    assert entry_lock_prefix("rejected") == ENTRY_LOCKED_PREFIX
