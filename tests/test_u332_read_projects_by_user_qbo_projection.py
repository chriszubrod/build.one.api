"""Pure-logic test for U-332: `ReadProjectsByUserId`'s SELECT lists now project
`[QboId]`/`[RealmId]` (`entities/project/sql/dbo.project.sql`), matching
`ReadProjectById`'s shape.

Follow-up to U-327, which closed the same gap on the other three project read
sprocs (`ReadProjects`/`ReadProjectByPublicId`/`ReadProjectByName`) but left
`ReadProjectsByUserId` -- the user-scoped list path (`ProjectService`/
`ProjectRepository.read_by_user_id`) -- still stripping identity. That sproc has
TWO SELECT variants: a system-admin block (all projects) and a non-admin block
(`INNER JOIN dbo.[UserProject]`). Before this fix, neither projected
QboId/RealmId, so a non-admin listing their own projects saw every project's
QBO identity as `None` even when set.

`ProjectRepository._from_db` already `getattr`s `QboId`/`RealmId` (proven by the
`ReadProjectById` / `ReadProjectByQboIdAndRealmId` paths), so this exercises the
repo layer directly -- no live DB, per the pure-logic harness -- to prove
`read_by_user_id` surfaces identity end to end once the row carries those
columns, and still degrades safely when it doesn't."""
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


def _select_column_blocks(proc_name: str) -> list[frozenset[str]]:
    """Column set for EVERY `SELECT ... FROM` segment in a proc body. These
    bodies are simple single-table SELECTs with no bare `*`/views, so a
    bracketed-token scan of each select list is exact. Returns one frozenset per
    SELECT variant (ReadProjectsByUserId has two: admin + UserProject-scoped)."""
    body = _proc_body(proc_name)
    select_lists = re.findall(
        r"SELECT(?:\s+DISTINCT)?(.*?)\bFROM\b", body, re.IGNORECASE | re.DOTALL
    )
    assert select_lists, f"no SELECT...FROM segments found in {proc_name}"
    return [frozenset(_COL_TOKEN.findall(seg)) for seg in select_lists]


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


def _setup_mock_connection(mock_get_connection, *, fetchall=None):
    cursor = MagicMock()
    if fetchall is not None:
        cursor.fetchall.return_value = fetchall
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_get_connection.return_value.__enter__.return_value = conn
    return cursor


# Canonical identity-projecting shape (proven by U-327's suite).
BY_ID_COLUMNS = _select_column_blocks("ReadProjectById")[0]


def test_read_projects_by_user_both_blocks_match_read_by_id():
    """Static (no-DB) regression guard for the SQL change itself: fails red on
    the pre-fix file (both SELECT variants lacked QboId/RealmId) and green
    post-fix. Asserts BOTH the system-admin block and the UserProject-scoped
    block project identity and match ReadProjectById's SELECT shape -- the
    task's acceptance criterion."""
    blocks = _select_column_blocks("ReadProjectsByUserId")
    assert len(blocks) == 2, f"expected admin + scoped SELECT variants, got {len(blocks)}"
    for columns in blocks:
        assert {"QboId", "RealmId"} <= columns
        assert columns == BY_ID_COLUMNS


@patch("entities.project.persistence.repo.get_connection")
def test_read_by_user_id_surfaces_qbo_identity_when_set(mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection, fetchall=[_mock_row()])

    projects = ProjectRepository().read_by_user_id(17, actor_is_system_admin=False)

    assert len(projects) == 1
    assert projects[0].qbo_id == "1479"
    assert projects[0].realm_id == "realm-1"
    executed_sql = cursor.execute.call_args[0][0]
    assert "ReadProjectsByUserId" in executed_sql


@patch("entities.project.persistence.repo.get_connection")
def test_read_by_user_id_defaults_identity_to_none_when_row_lacks_it(mock_get_connection):
    """A row shaped like the PRE-fix sproc (no QboId/RealmId attributes at all)
    must not raise -- `_from_db`'s `getattr(row, "QboId", None)` default is the
    safety net this fix relies on, not a new behavior."""
    row = _mock_row()
    del row.QboId
    del row.RealmId
    _setup_mock_connection(mock_get_connection, fetchall=[row])

    projects = ProjectRepository().read_by_user_id(17, actor_is_system_admin=False)

    assert projects[0].qbo_id is None
    assert projects[0].realm_id is None
