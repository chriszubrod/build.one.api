"""U-633 — time-log UPDATE with no matched row maps to 409 (stale row version) or 404 (gone)."""

import inspect
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.time_entry.api.router import update_time_log
from entities.time_entry.business.model import TimeLog
from entities.time_entry.business.time_log_service import TimeLogService
from entities.time_entry.persistence.time_log_repo import TimeLogRepository
from shared.api.errors import ErrorCode
from shared.api.responses import classify_database_error
from shared.database import (
    DatabaseConcurrencyError,
    RecordNotFoundError,
    RowVersionConflictError,
    map_database_error,
)


LOG_PUBLIC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UPDATE_PATH = f"/api/v1/time-logs/{LOG_PUBLIC_ID}"

_ROW_VERSION_CONFLICT_DETAIL = (
    "Update did not match any row; the time log may have been modified by another process "
    "(row-version conflict), is not accessible to this user, or no longer exists."
)


def _sample_time_log(**overrides) -> TimeLog:
    defaults = dict(
        id=42,
        public_id=LOG_PUBLIC_ID,
        row_version="AAAAAAAAAAE=",
        created_datetime="2026-01-01 00:00:00",
        modified_datetime="2026-01-02 00:00:00",
        time_entry_id=7,
        clock_in="2026-01-01 08:00:00",
        clock_out="2026-01-01 17:00:00",
        log_type="work",
        duration=Decimal("9"),
        latitude=None,
        longitude=None,
        project_id=1,
        note=None,
    )
    defaults.update(overrides)
    return TimeLog(**defaults)


@contextmanager
def _fake_connection(fetchone_result):
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_result
    conn = MagicMock()
    conn.cursor.return_value = cursor
    yield conn


def test_classify_database_error_maps_row_version_conflict_to_409():
    api_error = classify_database_error(RowVersionConflictError(_ROW_VERSION_CONFLICT_DETAIL))
    assert api_error is not None
    assert api_error.status_code == 409
    assert api_error.error_code == ErrorCode.CONCURRENCY_CONFLICT
    assert _ROW_VERSION_CONFLICT_DETAIL not in str(api_error.detail)


def test_classify_database_error_plain_concurrency_is_not_409():
    assert classify_database_error(DatabaseConcurrencyError("Concurrency violation: x")) is None


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def _override_time_log_rbac():
    rbac_dependency = inspect.signature(update_time_log).parameters[
        "current_user"
    ].default.dependency
    app.dependency_overrides[rbac_dependency] = lambda: {"sub": "test-sub"}


def test_update_time_log_route_returns_409_on_row_version_conflict(
    client, clear_dependency_overrides
):
    _override_time_log_rbac()
    conflict = RowVersionConflictError(_ROW_VERSION_CONFLICT_DETAIL)
    with patch.object(
        TimeLogService,
        "update_by_public_id",
        side_effect=conflict,
    ):
        response = client.put(
            UPDATE_PATH,
            json={"row_version": "AAAAAAAAAAI="},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "concurrency_conflict"


def test_update_time_log_route_bare_concurrency_is_not_409(
    client, clear_dependency_overrides
):
    _override_time_log_rbac()
    concurrency = DatabaseConcurrencyError(
        "Concurrency violation: Update did not match any row …"
    )
    with patch.object(
        TimeLogService,
        "update_by_public_id",
        side_effect=concurrency,
    ):
        response = client.put(
            UPDATE_PATH,
            json={"row_version": "AAAAAAAAAAI="},
        )
    assert response.status_code != 409
    assert response.status_code >= 500


def test_deadlock_on_update_is_not_409(client, clear_dependency_overrides):
    _override_time_log_rbac()
    deadlock = DatabaseConcurrencyError(
        "Concurrency violation: Transaction (Process ID 55) was deadlocked "
        "on lock resources with another process and has been chosen as the "
        "deadlock victim. Rerun the transaction. (1205)"
    )
    with patch.object(
        TimeLogService,
        "update_by_public_id",
        side_effect=deadlock,
    ):
        response = client.put(
            UPDATE_PATH,
            json={"row_version": "AAAAAAAAAAI="},
        )
    assert response.status_code != 409
    assert response.status_code >= 500


def test_update_time_log_route_returns_404_when_row_vanished(
    client, clear_dependency_overrides
):
    _override_time_log_rbac()
    missing = RecordNotFoundError("TimeLog with public_id 'x' not found.")
    with patch.object(
        TimeLogService,
        "update_by_public_id",
        side_effect=missing,
    ):
        response = client.put(
            UPDATE_PATH,
            json={"row_version": "AAAAAAAAAAI="},
        )
    assert response.status_code == 404
    assert response.json()["error_code"] == "not_found"


def test_unnumbered_unique_violation_on_clockin_index_stays_422():
    raw = (
        "Violation of UNIQUE KEY constraint 'UX_TimeLog_TimeEntryId_ClockIn'. "
        "Cannot insert duplicate key in object 'dbo.TimeLog'."
    )
    mapped = map_database_error(Exception(raw))
    assert isinstance(mapped, DatabaseConcurrencyError)
    api_error = classify_database_error(mapped)
    assert api_error is not None
    assert api_error.status_code != 409
    assert api_error.status_code == 422
    assert api_error.error_code == ErrorCode.DUPLICATE_KEY


def test_update_by_id_no_row_then_present_raises_row_version_conflict():
    repo = TimeLogRepository()
    time_log = _sample_time_log()
    read_mock = MagicMock(return_value=_sample_time_log(row_version="AAAAAAAAAAI="))
    with patch(
        "entities.time_entry.persistence.time_log_repo.get_connection",
        side_effect=lambda *a, **k: _fake_connection(None),
    ):
        with patch.object(TimeLogRepository, "read_by_id", read_mock):
            with pytest.raises(RowVersionConflictError):
                repo.update_by_id(
                    time_log,
                    actor_user_id=17,
                    actor_is_system_admin=False,
                    actor_can_view_team=True,
                )
    read_mock.assert_called_once_with(
        time_log.id,
        actor_user_id=17,
        actor_is_system_admin=False,
        actor_can_view_team=True,
    )


def test_update_by_id_no_row_then_missing_raises_record_not_found():
    repo = TimeLogRepository()
    time_log = _sample_time_log()
    read_mock = MagicMock(return_value=None)
    with patch(
        "entities.time_entry.persistence.time_log_repo.get_connection",
        side_effect=lambda *a, **k: _fake_connection(None),
    ):
        with patch.object(TimeLogRepository, "read_by_id", read_mock):
            with pytest.raises(RecordNotFoundError, match=r"^TimeLog with public_id .+ not found\.$"):
                repo.update_by_id(
                    time_log,
                    actor_user_id=17,
                    actor_is_system_admin=False,
                    actor_can_view_team=True,
                )
    read_mock.assert_called_once_with(
        time_log.id,
        actor_user_id=17,
        actor_is_system_admin=False,
        actor_can_view_team=True,
    )


def test_update_by_public_id_missing_row_raises_record_not_found():
    service = TimeLogService(repo=MagicMock())
    service.repo.read_by_public_id.return_value = None
    with patch(
        "entities.time_entry.business.time_log_service._actor_scope",
        return_value=(17, False, True),
    ):
        with pytest.raises(RecordNotFoundError, match=r"^TimeLog with public_id .+ not found\.$"):
            service.update_by_public_id(
                public_id=LOG_PUBLIC_ID,
                row_version="AAAAAAAAAAI=",
            )


@contextmanager
def _raising_connection(message):
    cursor = MagicMock()
    cursor.execute.side_effect = Exception(message)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    yield conn


def test_update_by_id_deadlock_from_driver_is_base_concurrency_not_row_version():
    """A deadlock victim raised by `cursor.execute` never reaches the typed branches:
    it maps to the BASE class (unclassified → 5xx), never to the 409 subclass."""
    repo = TimeLogRepository()
    read_mock = MagicMock()
    with patch(
        "entities.time_entry.persistence.time_log_repo.get_connection",
        side_effect=lambda *a, **k: _raising_connection(
            "Transaction (Process ID 55) was deadlocked on lock resources with another process "
            "and has been chosen as the deadlock victim. Rerun the transaction. (1205)"
        ),
    ):
        with patch.object(TimeLogRepository, "read_by_id", read_mock):
            with pytest.raises(DatabaseConcurrencyError) as excinfo:
                repo.update_by_id(_sample_time_log(), actor_user_id=17, actor_is_system_admin=False, actor_can_view_team=True)
    assert not isinstance(excinfo.value, RowVersionConflictError)
    assert not isinstance(excinfo.value, RecordNotFoundError)
    assert classify_database_error(excinfo.value) is None
    read_mock.assert_not_called()
