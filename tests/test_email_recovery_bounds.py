from contextlib import contextmanager
from types import SimpleNamespace

from entities.email_message.business.service import EmailMessageService
from intelligence.persistence.session_repo import AgentSessionRepo


def _patch_db(monkeypatch, module_path, row_attrs):
    """Monkeypatch get_connection + call_procedure on a repo module."""
    captured = {}

    class _FakeCursor:
        def fetchone(self):
            return SimpleNamespace(**row_attrs)

    @contextmanager
    def _fake_conn():
        yield SimpleNamespace(cursor=lambda: _FakeCursor())

    def _capture_call_procedure(*, cursor, name, params):
        captured["name"] = name
        captured["params"] = params

    monkeypatch.setattr(f"{module_path}.get_connection", _fake_conn)
    monkeypatch.setattr(f"{module_path}.call_procedure", _capture_call_procedure)
    return captured


def test_email_recover_stuck_default_max_rows(monkeypatch):
    captured = _patch_db(
        monkeypatch,
        "entities.email_message.persistence.repo",
        {"ResetCount": 0, "FailedCount": 0},
    )

    EmailMessageService().recover_stuck_processing()

    assert captured["name"] == "RecoverStuckProcessingEmailMessages"
    assert captured["params"]["MaxRows"] == 50


def test_email_recover_stuck_custom_max_rows(monkeypatch):
    captured = _patch_db(
        monkeypatch,
        "entities.email_message.persistence.repo",
        {"ResetCount": 0, "FailedCount": 0},
    )

    EmailMessageService().recover_stuck_processing(max_rows=7)

    assert captured["params"]["MaxRows"] == 7
    assert captured["params"]["StaleAfterMinutes"] == 10
    assert captured["params"]["MaxResets"] == 3


def test_session_timeout_default_max_rows(monkeypatch):
    captured = _patch_db(
        monkeypatch,
        "intelligence.persistence.session_repo",
        {
            "TimedOutSessionCount": 0,
            "LinkedEmailResetCount": 0,
            "LinkedEmailFailedCount": 0,
        },
    )

    AgentSessionRepo().timeout_long_running()

    assert captured["name"] == "TimeoutLongRunningAgentSessions"
    assert captured["params"]["MaxRows"] == 50
