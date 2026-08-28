"""Pure-logic test for U-327: `ReadProjects`/`ReadProjectByPublicId`/
`ReadProjectByName`'s SELECT lists now project `[QboId]`/`[RealmId]`
(`entities/project/sql/dbo.project.sql`), matching `ReadProjectById`'s shape.

Before this fix, only `ReadProjectById` projected identity -- a project's
QboId/RealmId was invisible to any caller of `ProjectService.read_all()` /
`read_by_public_id()` / `read_by_name()`. Booked in TODO.md by U-312, which
worked around the gap in-file (re-fetching via `read_by_id`) rather than
editing this shared base SQL file. `ProjectRepository._from_db` already
`getattr`s `QboId`/`RealmId` (proven by the existing `ReadProjectById` /
`ReadProjectByQboIdAndRealmId` paths), so this test exercises the repo layer
directly -- no live DB, per the pure-logic harness -- to prove `read_all`,
`read_by_public_id`, and `read_by_name` now surface identity end to end once
the row carries those columns, and still degrade safely when it doesn't."""
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.project.persistence.repo import ProjectRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_SQL = REPO_ROOT / "entities" / "project" / "sql" / "dbo.project.sql"

_COL_TOKEN = re.compile(r"\[(\w+)\]")


def _proc_body(proc_name: str) -> str:
    """Static (no-DB) isolation of one named CREATE PROCEDURE batch's body in
    dbo.project.sql, up to its closing `END;` line."""
    text = _PROJECT_SQL.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+(?:\[?dbo\]?\s*\.\s*)?\[?{proc_name}\b\]?"
        rf"(.*?)^END;",
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(text)
    assert match, f"could not isolate {proc_name}'s body in {_PROJECT_SQL}"
    return match.group(1)


def _select_columns(proc_name: str) -> frozenset[str]:
    """SELECT-list column set for a proc body -- these bodies are simple
    single-table SELECTs with no bare `*`/views, so a bracketed-token scan up
    to the first FROM is exact."""
    body = _proc_body(proc_name)
    select_list = body[: body.upper().index("FROM")]
    return frozenset(_COL_TOKEN.findall(select_list))


def _mock_row(**kwargs):
    defaults = {
        "Id": 202,
        "PublicId": "00000000-0000-0000-0000-000000000202",
        "RowVersion": b"\x00\x00\x00\x00\x00\x00\x00\x01",
        "CreatedDatetime": "2026-01-01 00:00:00",
        "ModifiedDatetime": "2026-01-02 00:00:00",
        "Name": "CRS - 425 Craighead St",
        "Description": "Renovation",
        "Status": "active",
        "CustomerId": 9,
        "Abbreviation": "CRS",
        "Notes": None,
        "QboId": "1479",
        "RealmId": "realm-1",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _setup_mock_connection(mock_get_connection, *, fetchone=None, fetchall=None):
    cursor = MagicMock()
    if fetchone is not None:
        cursor.fetchone.return_value = fetchone
    if fetchall is not None:
        cursor.fetchall.return_value = fetchall
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_get_connection.return_value.__enter__.return_value = conn
    return cursor


BY_ID_COLUMNS = _select_columns("ReadProjectById")


def test_read_projects_select_shape_matches_read_by_id():
    """Static (no-DB) regression guard for the SQL change itself: fails red on
    the pre-fix file (ReadProjects lacked QboId/RealmId) and green post-fix --
    the mocked-row tests below only prove the Python read-through path, which
    was already correct; this proves the base SQL file's SELECT list actually
    changed, matching the task's own acceptance criterion ("match
    ReadProjectById's SELECT shape")."""
    columns = _select_columns("ReadProjects")
    assert {"QboId", "RealmId"} <= columns
    assert columns == BY_ID_COLUMNS


def test_read_project_by_public_id_select_shape_matches_read_by_id():
    columns = _select_columns("ReadProjectByPublicId")
    assert {"QboId", "RealmId"} <= columns
    assert columns == BY_ID_COLUMNS


def test_read_project_by_name_select_shape_matches_read_by_id():
    columns = _select_columns("ReadProjectByName")
    assert {"QboId", "RealmId"} <= columns
    assert columns == BY_ID_COLUMNS


@patch("entities.project.persistence.repo.get_connection")
def test_read_all_surfaces_qbo_identity_when_set(mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection, fetchall=[_mock_row()])

    projects = ProjectRepository().read_all(actor_user_id=17, actor_is_system_admin=True)

    assert len(projects) == 1
    assert projects[0].qbo_id == "1479"
    assert projects[0].realm_id == "realm-1"
    executed_sql = cursor.execute.call_args[0][0]
    assert "ReadProjects" in executed_sql


@patch("entities.project.persistence.repo.get_connection")
def test_read_by_public_id_surfaces_qbo_identity_when_set(mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection, fetchone=_mock_row())

    project = ProjectRepository().read_by_public_id(
        "00000000-0000-0000-0000-000000000202", actor_user_id=17, actor_is_system_admin=True
    )

    assert project.qbo_id == "1479"
    assert project.realm_id == "realm-1"
    executed_sql = cursor.execute.call_args[0][0]
    assert "ReadProjectByPublicId" in executed_sql


@patch("entities.project.persistence.repo.get_connection")
def test_read_by_name_surfaces_qbo_identity_when_set(mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection, fetchone=_mock_row())

    project = ProjectRepository().read_by_name(
        "CRS - 425 Craighead St", actor_user_id=17, actor_is_system_admin=True
    )

    assert project.qbo_id == "1479"
    assert project.realm_id == "realm-1"
    executed_sql = cursor.execute.call_args[0][0]
    assert "ReadProjectByName" in executed_sql


@patch("entities.project.persistence.repo.get_connection")
def test_read_all_defaults_identity_to_none_when_row_lacks_it(mock_get_connection):
    """A row shaped like the PRE-fix sproc (no QboId/RealmId attributes at
    all) must not raise -- `_from_db`'s `getattr(row, "QboId", None)` default
    is the safety net this fix relies on, not a new behavior."""
    row = _mock_row()
    del row.QboId
    del row.RealmId
    _setup_mock_connection(mock_get_connection, fetchall=[row])

    projects = ProjectRepository().read_all(actor_user_id=17, actor_is_system_admin=True)

    assert projects[0].qbo_id is None
    assert projects[0].realm_id is None


@patch("entities.project.persistence.repo.get_connection")
def test_read_by_public_id_defaults_identity_to_none_when_row_lacks_it(mock_get_connection):
    row = _mock_row()
    del row.QboId
    del row.RealmId
    _setup_mock_connection(mock_get_connection, fetchone=row)

    project = ProjectRepository().read_by_public_id(
        "00000000-0000-0000-0000-000000000202", actor_user_id=17, actor_is_system_admin=True
    )

    assert project.qbo_id is None
    assert project.realm_id is None


@patch("entities.project.persistence.repo.get_connection")
def test_read_by_name_defaults_identity_to_none_when_row_lacks_it(mock_get_connection):
    row = _mock_row()
    del row.QboId
    del row.RealmId
    _setup_mock_connection(mock_get_connection, fetchone=row)

    project = ProjectRepository().read_by_name(
        "CRS - 425 Craighead St", actor_user_id=17, actor_is_system_admin=True
    )

    assert project.qbo_id is None
    assert project.realm_id is None
