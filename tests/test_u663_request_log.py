"""U-663 — one INFO audit line per time-tracking write + span attribute helper."""

import inspect
import logging
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.time_entry.api.router import create_time_entry, update_time_log
from entities.time_entry.business.model import TimeEntry
from entities.time_entry.business.time_log_service import TimeLogService
from shared.observability import EXPORTED_INFO_LOGGERS

ENTRY_PATH = "/api/v1/time-entries"
LOG_PUBLIC_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
UPDATE_LOG_PATH = f"/api/v1/time-logs/{LOG_PUBLIC_ID}"
GET_ENTRY_PATH = f"/api/v1/time-entries/{LOG_PUBLIC_ID}"


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def _override_rbac(route_fn, *, user_id=42):
    from fastapi import Request

    rbac_dependency = inspect.signature(route_fn).parameters["current_user"].default.dependency

    async def _fake_user(request: Request):
        request.state.user_id = user_id
        return {"sub": "test-sub", "user_id": user_id}

    app.dependency_overrides[rbac_dependency] = _fake_user


def _time_write_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == "shared.api.request_log" and getattr(r, "event_name", None) == "api.time_write"
    ]


def test_request_log_logger_is_in_exported_info_loggers():
    assert "shared.api.request_log" in EXPORTED_INFO_LOGGERS


def test_post_time_entry_emits_one_time_write_record(client, clear_dependency_overrides, caplog):
    _override_rbac(create_time_entry, user_id=99)
    fixture = TimeEntry(
        id=1,
        public_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        row_version="AAAAAAAAAAE=",
        created_datetime="2026-01-01 00:00:00",
        modified_datetime="2026-01-01 00:00:00",
        user_id=99,
        work_date="2026-01-01",
        note=None,
    )
    with caplog.at_level(logging.INFO, logger="shared.api.request_log"):
        with patch("entities.time_entry.api.router._resolve_user_id", return_value=99):
            with patch("entities.time_entry.api.router.ProcessEngine") as engine_cls:
                engine_cls.return_value.execute_synchronous.return_value = {
                    "success": True,
                    "data": fixture.to_dict(),
                }
                response = client.post(
                    ENTRY_PATH,
                    json={
                        "user_public_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                        "work_date": "2026-01-01",
                    },
                )
    assert response.status_code == 201
    records = _time_write_records(caplog)
    assert len(records) == 1
    rec = records[0]
    assert rec.method == "POST"
    assert rec.route == "/api/v1/time-entries"
    assert rec.status == 201
    assert rec.duration_ms >= 0
    assert rec.user_id == 99
    assert rec.error_code is None
    assert rec.getMessage() == "time_write"


def test_put_time_log_422_emits_time_write_with_error_code(client, clear_dependency_overrides, caplog):
    from shared.api.responses import ENTRY_APPROVED_PREFIX

    _override_rbac(update_time_log)
    msg = (
        f"{ENTRY_APPROVED_PREFIX}Cannot modify time logs when time entry is in 'approved' status. "
        "Only 'draft' entries can have their time logs modified."
    )
    with caplog.at_level(logging.INFO, logger="shared.api.request_log"):
        with patch.object(TimeLogService, "update_by_public_id", side_effect=ValueError(msg)):
            response = client.put(UPDATE_LOG_PATH, json={"row_version": "AAAAAAAAAAI="})
    assert response.status_code == 422
    assert response.json()["error_code"] == "entry_approved"
    records = _time_write_records(caplog)
    assert len(records) == 1
    assert records[0].status == 422
    assert records[0].error_code == "entry_approved"


def test_get_time_entry_does_not_emit_time_write(client, clear_dependency_overrides, caplog):
    from entities.time_entry.api.router import read_time_entry

    _override_rbac(read_time_entry)
    with caplog.at_level(logging.INFO, logger="shared.api.request_log"):
        with patch(
            "entities.time_entry.business.service.TimeEntryService.read_by_public_id",
            return_value=None,
        ):
            response = client.get(GET_ENTRY_PATH)
    assert response.status_code == 404
    assert _time_write_records(caplog) == []


def test_post_bills_does_not_emit_time_write(client, clear_dependency_overrides, caplog):
    from entities.bill.api.router import create_bill_router

    _override_rbac(create_bill_router)
    with caplog.at_level(logging.INFO, logger="shared.api.request_log"):
        response = client.post("/api/v1/bills", json={})
    assert response.status_code >= 400
    assert _time_write_records(caplog) == []


def test_time_write_route_uses_template_not_raw_uuid(client, clear_dependency_overrides, caplog):
    _override_rbac(update_time_log)
    msg = "Some unprefixed error"
    with caplog.at_level(logging.INFO, logger="shared.api.request_log"):
        with patch(
            "entities.time_entry.business.time_log_service.TimeLogService.update_by_public_id",
            side_effect=ValueError(msg),
        ):
            response = client.put(UPDATE_LOG_PATH, json={"row_version": "AAAAAAAAAAI="})
    assert response.status_code == 400
    records = _time_write_records(caplog)
    assert len(records) == 1
    assert records[0].route == "/api/v1/time-logs/{public_id}"
    assert LOG_PUBLIC_ID not in records[0].route
    assert LOG_PUBLIC_ID not in str(records[0].__dict__)


class _RecordingSpan:
    def __init__(self):
        self.calls = []

    def set_attribute(self, key, value):
        self.calls.append((key, value))

    def is_recording(self):
        return True


def test_set_request_span_attributes_sets_keys_and_skips_none(monkeypatch):
    import shared.api.request_log as rl

    span = _RecordingSpan()
    trace_mod = types.ModuleType("opentelemetry.trace")
    trace_mod.get_current_span = lambda: span
    otel = types.ModuleType("opentelemetry")
    otel.trace = trace_mod
    monkeypatch.setitem(sys.modules, "opentelemetry", otel)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_mod)

    rl.set_request_span_attributes(
        **{"enduser.id": "17", "buildone.company_id": "1", "buildone.error_code": None},
    )
    assert span.calls == [("enduser.id", "17"), ("buildone.company_id", "1")]


def test_set_request_span_attributes_swallows_a_throwing_span(monkeypatch):
    """The helper runs inside the error handlers — a span that raises must not turn a
    clean 422 into a 500."""
    import shared.api.request_log as rl

    class _ThrowingSpan:
        def set_attribute(self, key, value):
            raise RuntimeError("exporter is broken")

    trace_mod = types.ModuleType("opentelemetry.trace")
    trace_mod.get_current_span = lambda: _ThrowingSpan()
    otel = types.ModuleType("opentelemetry")
    otel.trace = trace_mod
    monkeypatch.setitem(sys.modules, "opentelemetry", otel)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_mod)

    assert rl.set_request_span_attributes(**{"enduser.id": "17"}) is None


def test_set_request_span_attributes_no_op_when_opentelemetry_missing(monkeypatch):
    """`None` in sys.modules makes `import opentelemetry` raise ImportError regardless of
    whether the distro is installed — the helper must swallow it and set nothing."""
    import shared.api.request_log as rl

    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)
    assert rl.set_request_span_attributes(**{"enduser.id": "1"}) is None


def test_http_exception_handler_calls_set_request_span_attributes():
    import asyncio

    from starlette.datastructures import State

    from shared.api.errors import ApiError, ErrorCode, http_exception_handler

    recorded = []

    def _capture(**kwargs):
        recorded.append(kwargs)

    request = MagicMock()
    request.state = State({})
    exc = ApiError(status_code=422, detail="x", error_code=ErrorCode.ENTRY_APPROVED)

    with patch("shared.api.errors.set_request_span_attributes", side_effect=_capture):
        asyncio.run(http_exception_handler(request, exc))

    assert recorded == [{"buildone.error_code": ErrorCode.ENTRY_APPROVED}]
    assert request.state.error_code == ErrorCode.ENTRY_APPROVED


def test_validation_and_not_accessible_handlers_record_error_code():
    """Every error-body producer stamps request.state.error_code, not just HTTPException."""
    import asyncio

    from fastapi.exceptions import RequestValidationError
    from starlette.datastructures import State

    from shared.access import EntityNotAccessibleError
    from shared.api.errors import (
        ErrorCode,
        entity_not_accessible_handler,
        request_validation_handler,
    )

    request = MagicMock()
    request.state = State({})
    with patch("shared.api.errors.set_request_span_attributes"):
        asyncio.run(request_validation_handler(request, RequestValidationError([])))
    assert request.state.error_code == ErrorCode.VALIDATION_ERROR

    request = MagicMock()
    request.state = State({})
    with patch("shared.api.errors.set_request_span_attributes"):
        asyncio.run(entity_not_accessible_handler(request, EntityNotAccessibleError("TimeLog", 7)))
    assert request.state.error_code == ErrorCode.NOT_FOUND


def _scope(method="PUT", path=UPDATE_LOG_PATH, **extra):
    scope = {"type": "http", "method": method, "path": path, "state": {}}
    scope.update(extra)
    return scope


async def _noop_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def test_middleware_logs_status_500_when_the_app_raises_and_re_raises(caplog):
    """An exception escaping the app bypasses every response hook — the audit line
    must still land (status 500) and the exception must still propagate."""
    import asyncio

    from shared.api.request_log import TimeTrackingWriteLogMiddleware

    async def exploding_app(scope, receive, send):
        raise RuntimeError("boom")

    sent = []

    async def send(message):
        sent.append(message)

    middleware = TimeTrackingWriteLogMiddleware(exploding_app)
    scope = _scope(state={"user_id": 7})
    with caplog.at_level(logging.INFO, logger="shared.api.request_log"):
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(middleware(scope, _noop_receive, send))
    records = _time_write_records(caplog)
    assert len(records) == 1
    assert records[0].status == 500
    assert records[0].user_id == 7
    assert sent == []


def test_middleware_unmatched_route_logs_placeholder_not_raw_path(caplog):
    """No matched route (e.g. a 404 before routing): log a constant, never the raw
    path — the raw path carries the row's public_id."""
    import asyncio

    from shared.api.request_log import UNMATCHED_ROUTE, TimeTrackingWriteLogMiddleware

    async def app_404(scope, receive, send):
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        pass

    middleware = TimeTrackingWriteLogMiddleware(app_404)
    with caplog.at_level(logging.INFO, logger="shared.api.request_log"):
        asyncio.run(middleware(_scope(), _noop_receive, send))
    records = _time_write_records(caplog)
    assert len(records) == 1
    assert records[0].route == UNMATCHED_ROUTE
    assert records[0].status == 404
    assert LOG_PUBLIC_ID not in str(records[0].__dict__)


def test_put_time_log_validation_error_emits_validation_error_code(client, clear_dependency_overrides, caplog):
    _override_rbac(update_time_log)
    with caplog.at_level(logging.INFO, logger="shared.api.request_log"):
        response = client.put(UPDATE_LOG_PATH, json=[1, 2, 3])
    assert response.status_code == 422
    records = _time_write_records(caplog)
    assert len(records) == 1
    assert records[0].status == 422
    assert records[0].error_code == "validation_error"
