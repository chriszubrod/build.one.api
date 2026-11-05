"""U-624a — CreateTimeEntry is find-or-create: one TimeEntry header per (UserId, WorkDate).

Pure-logic pins (no live DB): the sproc (not Python) owns the 'draft' status row, the
service refuses another worker's day without standing, the repo maps the sproc's [Created]
flag, the wire shape is unchanged, the bulk import makes ONE header per (worker, day) and
never merges into a pre-existing one, and the base SQL file carries the find-or-create
body plus the two filtered unique indexes.
"""

import logging
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.time_entry.business.bulk_import_service import TimeEntryBulkImportService
from entities.time_entry.business.model import TimeEntry
from entities.time_entry.business.service import TimeEntryService
from entities.time_entry.persistence.repo import TimeEntryRepository
from shared.access import EntityNotAccessibleError
from shared.authz import current_is_system_context
from tests.sproc_text import sproc_body, strip_sql_comments

SQL_PATH = Path(__file__).resolve().parent.parent / "entities" / "time_entry" / "sql" / "dbo.time_entry.sql"
PUBLIC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SERVICE = "entities.time_entry.business.service"


def _entry(**overrides) -> TimeEntry:
    defaults = dict(
        id=7,
        public_id=PUBLIC_ID,
        row_version="AAAAAAAAAAE=",
        created_datetime="2026-11-05 08:00:00",
        modified_datetime="2026-11-05 08:00:00",
        user_id=42,
        work_date="2026-11-05",
        note=None,
    )
    defaults.update(overrides)
    return TimeEntry(**defaults)


def _create(entry: TimeEntry, scope=(42, False, False), target_user_id=42, system=False, repo=None,
            created_by_user_id=17, user=...):
    """Run service.create with a mocked repo; scope = (actor_user_id, is_admin, can_view_team)."""
    repo = repo or MagicMock()
    repo.create.return_value = entry
    if user is ...:
        user = SimpleNamespace(id=target_user_id)
    token = current_is_system_context.set(system)
    try:
        with patch(f"{SERVICE}.UserService") as user_service, patch(
            f"{SERVICE}.TimeEntryStatusRepository"
        ) as status_repo_cls, patch(f"{SERVICE}._actor_scope", return_value=scope):
            user_service.return_value.read_by_public_id.return_value = user
            result = TimeEntryService(repo=repo).create(
                user_public_id="user-public-id", work_date="2026-11-05", created_by_user_id=created_by_user_id
            )
    finally:
        current_is_system_context.reset(token)
    return result, repo, status_repo_cls


@pytest.mark.parametrize("created", [True, False])
def test_create_never_writes_a_status_row_in_python(created):
    # The sproc owns the initial 'draft' row (same transaction, self-heal on find).
    result, _repo, status_repo_cls = _create(_entry(created=created))
    assert result.id == 7
    status_repo_cls.return_value.create.assert_not_called()


def test_create_legacy_sproc_without_created_column_writes_draft_once():
    # New image against the OLD sproc: created is None -> Python writes the 'draft' row.
    result, _repo, status_repo_cls = _create(_entry(created=None))
    assert result.id == 7
    status_repo_cls.return_value.create.assert_called_once_with(time_entry_id=7, status="draft", user_id=17)


def test_create_legacy_sproc_status_user_falls_back_to_worker():
    _result, _repo, status_repo_cls = _create(_entry(created=None), created_by_user_id=None, scope=(None, None, False), system=True)
    status_repo_cls.return_value.create.assert_called_once_with(time_entry_id=7, status="draft", user_id=42)


def test_create_found_header_hidden_from_view_team_actor_raises_not_accessible():
    repo = MagicMock()
    repo.read_by_public_id.return_value = None
    with pytest.raises(EntityNotAccessibleError):
        _create(_entry(user_id=40, created=False), scope=(41, False, True), target_user_id=40, repo=repo)
    repo.read_by_public_id.assert_called_once_with(
        public_id=PUBLIC_ID, actor_user_id=41, actor_is_system_admin=False, actor_can_view_team=True
    )


def test_create_found_header_visible_to_view_team_actor_is_returned():
    found = _entry(user_id=40, created=False)
    repo = MagicMock()
    repo.read_by_public_id.return_value = found
    result, _repo, _ = _create(found, scope=(41, False, True), target_user_id=40, repo=repo)
    assert result is found
    repo.read_by_public_id.assert_called_once()


def test_create_found_header_own_day_skips_scoped_reread():
    repo = MagicMock()
    _create(_entry(created=False), scope=(42, False, False), target_user_id=42, repo=repo)
    repo.read_by_public_id.assert_not_called()


def test_create_unknown_user_is_404_for_non_admin():
    with pytest.raises(EntityNotAccessibleError):
        _create(_entry(), scope=(41, False, True), user=None)


def test_create_unknown_user_is_400_message_for_admin():
    with pytest.raises(ValueError, match="not found") as exc:
        _create(_entry(), scope=(41, True, False), user=None)
    assert not isinstance(exc.value, EntityNotAccessibleError)


def test_create_unknown_user_is_400_message_in_system_context():
    # System context gets the descriptive error too (mutant: drop `or is_system_context`).
    with pytest.raises(ValueError, match="not found") as exc:
        _create(_entry(), scope=(None, False, False), user=None, system=True)
    assert not isinstance(exc.value, EntityNotAccessibleError)


def test_create_found_header_admin_skips_scoped_reread():
    # The scoped sproc lets an admin see every row; the re-read is skipped (mutant: re-read for admins too).
    repo = MagicMock()
    _create(_entry(user_id=40, created=False), scope=(41, True, False), target_user_id=40, repo=repo)
    repo.read_by_public_id.assert_not_called()


def test_create_found_header_returns_it_and_logs(caplog):
    found = _entry(created=False)
    with caplog.at_level(logging.INFO, logger=SERVICE):
        result, _repo, _status = _create(found)
    assert result is found
    records = [r for r in caplog.records if r.getMessage() == "time_entry.create.found_existing"]
    assert len(records) == 1 and records[0].id == 7


def test_create_for_another_worker_without_standing_raises_not_accessible():
    with pytest.raises(EntityNotAccessibleError):
        _create(_entry(user_id=40), scope=(41, False, False), target_user_id=40)


def test_create_for_another_worker_without_standing_never_reaches_repo():
    repo = MagicMock()
    with patch(f"{SERVICE}.UserService") as user_service, patch(
        f"{SERVICE}._actor_scope", return_value=(41, False, False)
    ):
        user_service.return_value.read_by_public_id.return_value = SimpleNamespace(id=40)
        with pytest.raises(EntityNotAccessibleError):
            TimeEntryService(repo=repo).create(user_public_id="u", work_date="2026-11-05")
    repo.create.assert_not_called()


def test_create_for_another_worker_with_view_team_allowed():
    result, repo, _ = _create(_entry(user_id=40), scope=(41, False, True), target_user_id=40)
    assert result.user_id == 40
    repo.create.assert_called_once()


def test_create_for_another_worker_as_system_admin_allowed():
    _result, repo, _ = _create(_entry(user_id=40), scope=(41, True, False), target_user_id=40)
    repo.create.assert_called_once()


def test_create_for_own_day_allowed():
    _result, repo, _ = _create(_entry(), scope=(42, False, False), target_user_id=42)
    repo.create.assert_called_once()


def test_create_in_system_context_allowed():
    _result, repo, _ = _create(_entry(user_id=40), scope=(None, None, False), target_user_id=40, system=True)
    repo.create.assert_called_once()


def test_create_with_no_actor_and_no_system_marker_is_refused():
    with pytest.raises(EntityNotAccessibleError):
        _create(_entry(user_id=40), scope=(None, None, False), target_user_id=40, system=False)


def _row(**extra):
    base = dict(
        Id=7,
        PublicId=PUBLIC_ID,
        RowVersion=b"\x00\x00\x00\x00\x00\x00\x00\x01",
        CreatedDatetime="2026-11-05 08:00:00",
        ModifiedDatetime="2026-11-05 08:00:00",
        UserId=42,
        WorkDate="2026-11-05",
        Note=None,
    )
    base.update(extra)
    return SimpleNamespace(**base)


def test_from_db_reads_created_flag_tri_state():
    repo = TimeEntryRepository()
    assert repo._from_db(_row(Created=False)).created is False
    assert repo._from_db(_row(Created=True)).created is True
    assert repo._from_db(_row()).created is None  # older sproc body: no [Created] column


def test_to_dict_exposes_created_only_when_known():
    assert "created" not in _entry(created=None).to_dict()
    assert _entry(created=True).to_dict()["created"] is True
    assert _entry(created=False).to_dict()["created"] is False


def _svc(created=True, entry_id=7):
    svc = TimeEntryBulkImportService.__new__(TimeEntryBulkImportService)
    svc.entry_service = MagicMock()
    svc.entry_service.create.return_value = _entry(id=entry_id, created=created)
    svc.log_service = MagicMock()
    svc.user_service = MagicMock()
    svc._resolve_worker_user_public_id = lambda row: "user-public-id"
    return svc


def _two_rows(**overrides):
    a = {"work_date": "2026-11-05", "hours": "3", "project_public_id": "p1", "note": "first"}
    b = {"work_date": "2026-11-05", "hours": "5", "project_public_id": "p2", "note": "second"}
    a.update(overrides.get("a", {}))
    b.update(overrides.get("b", {}))
    return [a, b]


def _run(svc, rows):
    with patch("entities.project.business.service.ProjectService") as project_service:
        project_service.return_value.read_by_public_id.side_effect = lambda public_id: SimpleNamespace(
            id=int(public_id[1:])
        )
        return svc.import_rows(rows)


def test_bulk_import_groups_rows_per_worker_day_one_header_two_logs_one_submit():
    svc = _svc(created=True)
    results = _run(svc, _two_rows())
    svc.entry_service.create.assert_called_once()
    assert svc.entry_service.create.call_args.kwargs["note"] == "first"
    calls = svc.log_service.create.call_args_list
    assert len(calls) == 2
    assert [c.kwargs["clock_in"] for c in calls] == ["2026-11-05 09:00:00", "2026-11-05 12:00:00"]
    assert [c.kwargs["clock_out"] for c in calls] == ["2026-11-05 12:00:00", "2026-11-05 17:00:00"]
    assert [c.kwargs["project_id"] for c in calls] == [1, 2]
    svc.entry_service.submit.assert_called_once_with(public_id=PUBLIC_ID, user_id=42)
    assert [r["status"] for r in results] == ["submitted", "submitted"]
    assert [r["row_index"] for r in results] == [0, 1]
    assert all(r["time_entry_public_id"] == PUBLIC_ID for r in results)


def test_bulk_import_found_header_fails_every_row_writes_nothing():
    svc = _svc(created=False, entry_id=99)
    results = _run(svc, _two_rows())
    svc.log_service.create.assert_not_called()
    svc.entry_service.submit.assert_not_called()
    assert [r["status"] for r in results] == ["failed", "failed"]
    for r in results:
        assert "(id 99)" in r["error"] and "never merges" in r["error"] and "2026-11-05" in r["error"]


def test_bulk_import_legacy_created_none_is_treated_as_created():
    svc = _svc(created=None)
    results = _run(svc, _two_rows())
    assert svc.log_service.create.call_count == 2
    svc.entry_service.submit.assert_called_once()
    assert [r["status"] for r in results] == ["submitted", "submitted"]


def test_bulk_import_mixed_submit_flags_submits_once():
    svc = _svc(created=True)
    results = _run(svc, _two_rows(a={"submit": False}, b={"submit": True}))
    svc.entry_service.submit.assert_called_once_with(public_id=PUBLIC_ID, user_id=42)
    assert [r["status"] for r in results] == ["submitted", "submitted"]


def test_bulk_import_no_landed_rows_reports_header_left_in_draft():
    svc = _svc(created=True)
    svc.log_service.create.side_effect = ValueError("boom")
    results = _run(svc, _two_rows())
    svc.entry_service.submit.assert_not_called()
    assert [r["status"] for r in results] == ["failed", "failed"]
    for r in results:
        assert r["time_entry_public_id"] == PUBLIC_ID
        assert r["error"] == "boom — header left in draft (import created it)"


def test_bulk_import_submit_failure_marks_landed_rows_failed_with_the_header_id():
    svc = _svc(created=True)
    svc.log_service.create.side_effect = [ValueError("boom"), None]
    svc.entry_service.submit.side_effect = ValueError("cannot submit")
    results = _run(svc, _two_rows())
    assert results[0] == {"row_index": 0, "status": "failed", "error": "boom"}
    assert results[1]["status"] == "failed"
    assert results[1]["time_entry_public_id"] == PUBLIC_ID
    assert results[1]["error"] == "cannot submit — header left in draft (import created it)"


def test_bulk_import_normalizes_work_date_for_grouping():
    svc = _svc(created=True)
    rows = _two_rows(b={"work_date": "2026-11-05T00:00:00"})
    results = _run(svc, rows)
    svc.entry_service.create.assert_called_once()
    assert svc.entry_service.create.call_args.kwargs["work_date"] == "2026-11-05"
    assert svc.log_service.create.call_count == 2
    assert [r["status"] for r in results] == ["submitted", "submitted"]


def test_bulk_import_invalid_work_date_fails_the_row():
    svc = _svc(created=True)
    results = _run(svc, _two_rows(a={"work_date": "not-a-date"}))
    assert results[0] == {"row_index": 0, "status": "failed", "error": "Row has an invalid work_date"}
    assert results[1]["status"] == "submitted"


def test_bulk_import_submit_false_group_reports_created():
    svc = _svc(created=True)
    results = _run(svc, _two_rows(a={"submit": False}, b={"submit": False}))
    assert svc.log_service.create.call_count == 2
    svc.entry_service.submit.assert_not_called()
    assert [r["status"] for r in results] == ["created", "created"]


def test_bulk_import_one_bad_row_does_not_sink_its_group():
    svc = _svc(created=True)
    svc.log_service.create.side_effect = [ValueError("boom"), None]
    results = _run(svc, _two_rows())
    assert svc.log_service.create.call_count == 2
    svc.entry_service.submit.assert_called_once()
    assert results[0]["status"] == "failed" and results[0]["error"] == "boom"
    assert results[1]["status"] == "submitted"


def test_bulk_import_separate_days_get_separate_headers():
    svc = _svc(created=True)
    rows = _two_rows(b={"work_date": "2026-11-06"})
    results = _run(svc, rows)
    assert svc.entry_service.create.call_count == 2
    assert svc.entry_service.submit.call_count == 2
    assert [r["status"] for r in results] == ["submitted", "submitted"]


def test_bulk_import_worker_resolution_error_fails_the_row():
    svc = _svc(created=True)

    def boom(row):
        raise ValueError("No User found")

    svc._resolve_worker_user_public_id = boom
    results = _run(svc, _two_rows())
    svc.entry_service.create.assert_not_called()
    assert [r["status"] for r in results] == ["failed", "failed"]
    assert all(r["error"] == "No User found" for r in results)


def test_synth_clock_times_offsets_clock_in():
    svc = _svc()
    assert svc._synth_clock_times("2026-11-05", Decimal("2")) == ("2026-11-05 09:00:00", "2026-11-05 11:00:00")
    assert svc._synth_clock_times("2026-11-05", Decimal("2"), start_offset_hours=Decimal("3")) == (
        "2026-11-05 12:00:00",
        "2026-11-05 14:00:00",
    )


def test_sproc_create_time_entry_is_find_or_create():
    body = sproc_body(SQL_PATH, "CreateTimeEntry")
    code = strip_sql_comments(body)
    assert "sp_getapplock" in code
    assert "SET NOCOUNT ON" in code
    assert "[Created]" in code
    lock_key = re.search(r"DECLARE @Lock\b[^;]*;", code).group(0)
    assert "@UserId" in lock_key and "@WorkDate" in lock_key
    found = re.search(r"SELECT TOP 1 @ExistingId.*?ORDER BY te\.\[Id\] ASC", code, re.S).group(0)
    assert "[UserId] = @UserId" in found and "[WorkDate] = @WorkDate" in found
    assert "ORDER BY te.[Id] ASC" in code
    assert len(re.findall(r"INSERT INTO dbo\.\[TimeEntry\]", code)) == 1
    status_inserts = re.findall(r"INSERT INTO dbo\.\[TimeEntryStatus\].*?;", code, re.S)
    assert len(status_inserts) == 2  # insert branch + self-heal
    assert all("'draft'" in stmt for stmt in status_inserts)
    # the self-heal must be guarded: an unconditional 'draft' on a found header would re-open paid days
    assert "IF NOT EXISTS (SELECT 1 FROM dbo.[TimeEntryStatus] WHERE [TimeEntryId] = @ExistingId)" in code
    assert "ROLLBACK" not in code.upper()
    assert code.upper().count("COMMIT TRANSACTION") >= 2  # lock-failure path + normal path
    assert "CAST(0 AS BIT) AS [Created]" in code and "CAST(1 AS BIT) AS [Created]" in code
    raiserror = re.search(r"RAISERROR\('([^']*)'", code).group(1).lower()
    assert "lock" not in raiserror and "already exists" not in raiserror and "duplicate" not in raiserror


def test_filtered_unique_indexes_defined_in_base_file():
    sql = SQL_PATH.read_text()
    for name, where in (
        ("UX_TimeEntry_UserId_WorkDate", r"WHERE \[WorkDate\] >= '2026-07-01'"),
        ("UX_TimeLog_TimeEntry_ClockIn", r"WHERE \[Id\] > 191"),
    ):
        m = re.search(
            rf"CREATE UNIQUE NONCLUSTERED INDEX {name} ON [^;]*?{where};\s*\nEND\s*\nGO\b", sql
        )
        assert m, f"{name} missing, unfiltered, or not GO-terminated"
        assert f"name = '{name}'" in sql  # idempotent IF NOT EXISTS guard
