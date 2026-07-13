from contextlib import contextmanager
from decimal import Decimal

from entities.time_entry.business.auto_submit_service import TimeEntryAutoSubmitService


def _fake_lock(acquired):
    """Stand in for shared.db_lock.app_lock, yielding a fixed acquired flag."""
    @contextmanager
    def _cm(*a, **k):
        yield acquired
    return _cm


def _patch_lock(monkeypatch, acquired):
    monkeypatch.setattr("shared.db_lock.app_lock", _fake_lock(acquired))


def _patch_mode_on(monkeypatch):
    class _Settings:
        time_autosubmit_mode = "on"

    monkeypatch.setattr("config.Settings", _Settings)


def test_skip_when_lock_held(monkeypatch):
    """Lock held by another sweep -> skip cleanly, process zero rows."""
    _patch_lock(monkeypatch, acquired=False)
    _patch_mode_on(monkeypatch)

    read_calls = []
    monkeypatch.setattr(
        TimeEntryAutoSubmitService,
        "_read_day_entries",
        staticmethod(lambda work_date: read_calls.append(work_date) or []),
    )

    result = TimeEntryAutoSubmitService().run_for_work_date("2026-07-12")

    assert result["status"] == "skipped"
    assert result["reason"] == "already_running"
    assert read_calls == []  # the sweep body never ran


def test_runs_when_lock_acquired(monkeypatch):
    """Lock acquired -> the sweep body runs under it (one clean draft submits)."""
    _patch_lock(monkeypatch, acquired=True)
    _patch_mode_on(monkeypatch)
    monkeypatch.setattr("shared.authz.context.set_authz_context", lambda **kwargs: None)
    monkeypatch.setattr(
        TimeEntryAutoSubmitService, "_resolve_actor_id", staticmethod(lambda: 17))
    monkeypatch.setattr(
        TimeEntryAutoSubmitService,
        "_read_day_entries",
        staticmethod(lambda work_date: [
            {"id": 1, "public_id": "p1", "user_id": 1, "status": "draft"},
        ]),
    )
    monkeypatch.setattr(
        TimeEntryAutoSubmitService,
        "_read_user_info",
        staticmethod(lambda user_ids: {1: ("Worker", False, "worker1")}),
    )
    monkeypatch.setattr(
        TimeEntryAutoSubmitService,
        "_evaluate",
        staticmethod(lambda public_id, today: (
            {"is_complete": True, "summary": {"total_work_hours": "8"}},
            Decimal("8"),
            3,
        )),
    )
    monkeypatch.setattr(
        TimeEntryAutoSubmitService, "_has_labor_row", staticmethod(lambda eid: True))

    submit_calls = []
    monkeypatch.setattr(
        TimeEntryAutoSubmitService,
        "_submit",
        staticmethod(lambda public_id, actor_id: submit_calls.append(public_id)),
    )

    result = TimeEntryAutoSubmitService().run_for_work_date("2026-07-12")

    assert result["status"] == "ok"
    assert result["submitted"] == 1
    assert submit_calls == ["p1"]  # sweep body executed under the lock
