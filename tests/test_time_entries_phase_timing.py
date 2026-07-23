import logging
from types import SimpleNamespace

from entities.time_entry.api.router import (
    _phase_log_line,
    _phase_timing_enabled as router_phase_timing_enabled,
    read_time_entries,
)
from shared.rbac import (
    SYSTEM_ADMIN_GRANT,
    _get_user_permissions,
    _phase_timing_enabled,
    invalidate_user_cache,
)


def test_flag_single_definition():
    # The router imports the shared/rbac.py definition — one flag, one home.
    assert router_phase_timing_enabled is _phase_timing_enabled


def test_phase_timing_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TIME_ENTRIES_PHASE_TIMING", raising=False)
    assert _phase_timing_enabled() is False


def test_phase_timing_true_only_when_env_true(monkeypatch):
    for raw in ("true", "TRUE", "  true  "):
        monkeypatch.setenv("TIME_ENTRIES_PHASE_TIMING", raw)
        assert _phase_timing_enabled() is True

    for raw in ("", "false", "1", "yes"):
        monkeypatch.setenv("TIME_ENTRIES_PHASE_TIMING", raw)
        assert _phase_timing_enabled() is False


def test_phase_log_line_smoke():
    phases = {
        "read_paginated": 0.05,
        "count": 0.01,
        "project_ids": 0.002,
        "statuses": 0.003,
        "logs": 0.0,
        "serialize": 0.004,
        "total_handler": 0.1234,
    }
    line = _phase_log_line(phases, rows=7, include_logs=True)
    assert "\n" not in line
    assert line.startswith("TIMING time-entries ")
    for key in phases:
        assert f"{key}=" in line
    assert "read_paginated=50.0ms" in line
    assert "total_handler=123.4ms" in line
    assert "rows=7" in line
    assert "include_logs=True" in line


_U084_SUB = "test-sub-u084"


def test_rbac_phase_timing_miss_logs_hit_silent(monkeypatch, caplog):
    invalidate_user_cache(_U084_SUB)
    monkeypatch.setenv("TIME_ENTRIES_PHASE_TIMING", "true")
    monkeypatch.setattr(
        "shared.rbac._resolve_permissions_from_db",
        lambda **kwargs: SYSTEM_ADMIN_GRANT,
    )

    user = {"sub": _U084_SUB, "is_system_admin": True}

    with caplog.at_level(logging.INFO, logger="shared.rbac"):
        caplog.clear()
        _get_user_permissions(user)
        miss_msgs = [
            r.message for r in caplog.records if r.message.startswith("TIMING rbac")
        ]
        assert len(miss_msgs) == 1
        assert miss_msgs[0].startswith("TIMING rbac cache=miss resolve=")

        # A cache hit is deliberately silent — it's ~0ms by construction and
        # this path runs on every authenticated request app-wide.
        caplog.clear()
        _get_user_permissions(user)
        assert [
            r.message for r in caplog.records if "TIMING rbac" in r.message
        ] == []

    invalidate_user_cache(_U084_SUB)
    monkeypatch.delenv("TIME_ENTRIES_PHASE_TIMING", raising=False)
    with caplog.at_level(logging.INFO, logger="shared.rbac"):
        caplog.clear()
        _get_user_permissions(user)
        assert [
            r.message for r in caplog.records if "TIMING rbac" in r.message
        ] == []

    invalidate_user_cache(_U084_SUB)


def test_read_time_entries_response_identity_with_phase_timing(monkeypatch, caplog):
    entry_payload = {
        "id": 42,
        "public_id": "00000000-0000-0000-0000-000000000042",
        "work_date": "2026-07-01",
    }
    fake_entry = SimpleNamespace(
        id=42,
        to_dict=lambda: dict(entry_payload),
    )

    class FakeTimeEntryService:
        def __init__(self):
            self.repo = SimpleNamespace(
                read_distinct_project_ids_for=lambda time_entry_ids: {
                    42: [7, 8],
                },
            )

        def read_paginated(self, **kwargs):
            return [fake_entry]

        def count(self, **kwargs):
            return 1

    class FakeTimeEntryStatusRepository:
        def read_current_by_time_entry_ids(self, time_entry_ids):
            return {42: SimpleNamespace(status="submitted")}

    monkeypatch.setattr(
        "entities.time_entry.api.router.TimeEntryService",
        FakeTimeEntryService,
    )
    monkeypatch.setattr(
        "entities.time_entry.persistence.time_entry_status_repo.TimeEntryStatusRepository",
        FakeTimeEntryStatusRepository,
    )

    # Explicit include_logs: calling the handler directly would otherwise
    # bind the truthy FastAPI Query default object and enter the real
    # TimeLogRepository DB path.
    monkeypatch.delenv("TIME_ENTRIES_PHASE_TIMING", raising=False)
    result_off = read_time_entries(include_logs=False)

    monkeypatch.setenv("TIME_ENTRIES_PHASE_TIMING", "true")
    with caplog.at_level(logging.INFO, logger="entities.time_entry.api.router"):
        caplog.clear()
        result_on = read_time_entries(include_logs=False)
        timing_records = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and r.message.startswith("TIMING time-entries")
        ]

    assert result_off == result_on
    assert len(timing_records) == 1
