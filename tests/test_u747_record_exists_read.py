"""U-747 round 3: the read-only existence check for the day's contract_labor ReviewNotification.

Static checks on the base SQL (the proc is GO-terminated, read-only, filtered by Kind, ProjectId and
WorkDate), plus the repo and service wiring with fakes. No database is touched.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from entities.review.business.review_notification_service import ReviewNotificationRecordService
from entities.review.persistence import review_notification_repo as repo_mod
from entities.review.persistence.review_notification_repo import ReviewNotificationRepository
from tests.sproc_text import REPO_ROOT

SQL_PATH = REPO_ROOT / "entities" / "review" / "sql" / "dbo.review_notification.sql"
PROC = "ReadContractLaborReviewNotificationByProjectAndWorkDate"


def _sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def _proc_text() -> str:
    text = _sql()
    start = text.index(f"CREATE OR ALTER PROCEDURE dbo.{PROC}")
    end = re.search(r"^\s*GO\s*$", text[start:], re.MULTILINE | re.IGNORECASE)
    assert end, "the proc batch is not GO-terminated"
    return text[start:start + end.start()]


def test_the_proc_is_defined_exactly_once():
    assert _sql().count(f"CREATE OR ALTER PROCEDURE dbo.{PROC}") == 1


def test_the_proc_is_gated_by_go_and_no_other_statement_follows_it_in_its_batch():
    text = _sql()
    start = text.index(f"CREATE OR ALTER PROCEDURE dbo.{PROC}")
    after = text[start:]
    go = re.search(r"^\s*GO\s*$", after, re.MULTILINE | re.IGNORECASE)
    assert go is not None
    assert after[:go.start()].count("CREATE ") == 1  # the proc's own header only, no batch swallowed


def test_the_proc_takes_project_and_work_date_with_the_column_types_of_the_table():
    body = _proc_text()
    assert "@ProjectId BIGINT" in body
    assert "@WorkDate DATE" in body
    table = _sql()
    assert re.search(r"\[ProjectId\]\s+BIGINT NULL", table)
    assert re.search(r"\[WorkDate\]\s+DATE NULL", table)


def test_the_proc_selects_the_contract_labor_record_for_project_and_date_and_nothing_else():
    body = _proc_text()
    assert "SET NOCOUNT ON" in body
    assert "SELECT TOP 1 [Id]" in body
    assert "FROM dbo.[ReviewNotification]" in body
    assert "[Kind] = N'contract_labor'" in body
    assert "[ProjectId] = @ProjectId" in body
    assert "[WorkDate] = @WorkDate" in body
    assert "ORDER BY [Id]" in body
    for write in ("INSERT", "UPDATE", "DELETE", "MERGE", "ROLLBACK"):
        assert not re.search(rf"\b{write}\b", body), write


def test_the_header_lists_the_new_proc():
    sprocs_line = [ln for ln in _sql().splitlines() if ln.startswith("-- Sprocs:")]
    assert len(sprocs_line) == 1
    assert PROC in {p.strip() for p in sprocs_line[0].removeprefix("-- Sprocs:").split(",")}


class _Row:
    def __init__(self, Id):
        self.Id = Id


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row):
        self._cursor = _Cursor(row)

    def cursor(self):
        return self._cursor


def _patch_db(monkeypatch, row):
    calls = []

    @contextmanager
    def fake_get_connection(*args, **kwargs):
        yield _Conn(row)

    def fake_call_procedure(*, cursor, name, params):
        calls.append((name, params))

    monkeypatch.setattr(repo_mod, "get_connection", fake_get_connection)
    monkeypatch.setattr(repo_mod, "call_procedure", fake_call_procedure)
    return calls


def test_repo_returns_the_record_id_and_passes_the_params_in_order(monkeypatch):
    calls = _patch_db(monkeypatch, _Row(42))
    found = ReviewNotificationRepository().read_contract_labor_record_id(project_id=9, work_date="2026-10-07")
    assert found == 42
    assert calls == [(PROC, {"ProjectId": 9, "WorkDate": "2026-10-07"})]


def test_repo_returns_none_when_no_record_exists(monkeypatch):
    _patch_db(monkeypatch, None)
    assert ReviewNotificationRepository().read_contract_labor_record_id(project_id=9, work_date="2026-10-07") is None


def test_service_reports_existence_from_the_repo_read():
    seen = []

    class FakeRepo:
        def read_contract_labor_record_id(self, *, project_id, work_date):
            seen.append((project_id, work_date))
            return 42 if project_id == 9 else None

    service = ReviewNotificationRecordService(repo=FakeRepo())
    assert service.contract_labor_record_exists(project_id=9, work_date="2026-10-07") is True
    assert service.contract_labor_record_exists(project_id=8, work_date="2026-10-07") is False
    assert seen == [(9, "2026-10-07"), (8, "2026-10-07")]
