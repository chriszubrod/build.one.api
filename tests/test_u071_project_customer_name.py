"""U-071: the project LIST sprocs LEFT JOIN dbo.Customer and project `CustomerName`;
by-id sprocs stay untouched; `_from_db` reads the column via getattr so an
un-reapplied sproc still maps. Pure-logic, no live DB."""
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.project.persistence.repo import ProjectRepository
from tests.sproc_text import sproc_body

REPO_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_SQL = REPO_ROOT / "entities" / "project" / "sql" / "dbo.project.sql"

_COL_TOKEN = re.compile(r"\[(\w+)\]")
_LIST_PROCS = ("ReadProjects", "ReadProjectsByUserId")
_BY_ID_PROCS = ("ReadProjectById", "ReadProjectByPublicId", "ReadProjectByName")


def _select_lists(proc_name: str) -> list[str]:
    return re.findall(
        r"SELECT(?:\s+DISTINCT)?(.*?)\bFROM\b", sproc_body(_PROJECT_SQL, proc_name), re.IGNORECASE | re.DOTALL
    )


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
        "CustomerName": "Acme Builders",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _setup_mock_connection(mock_get_connection, *, fetchall):
    cursor = MagicMock()
    cursor.fetchall.return_value = fetchall
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_get_connection.return_value.__enter__.return_value = conn
    return cursor


def test_from_db_maps_customer_name_into_model_and_dict():
    project = ProjectRepository()._from_db(_mock_row())

    assert project.customer_name == "Acme Builders"
    assert project.to_dict()["customer_name"] == "Acme Builders"


def test_from_db_defaults_customer_name_when_row_lacks_it():
    row = _mock_row()
    del row.CustomerName

    project = ProjectRepository()._from_db(row)

    assert project.customer_name is None


def test_list_procs_project_customer_name_in_every_select_list():
    # ReadProjects' first SELECT is the list; its EXISTS subquery's `SELECT 1` is not.
    assert "CustomerName" in set(_COL_TOKEN.findall(_select_lists("ReadProjects")[0]))
    blocks = _select_lists("ReadProjectsByUserId")
    assert len(blocks) == 2, f"expected admin + scoped SELECT variants, got {len(blocks)}"
    for segment in blocks:
        assert "CustomerName" in set(_COL_TOKEN.findall(segment))


def test_list_procs_left_join_customer_on_project_customer_id():
    for proc in _LIST_PROCS:
        body = sproc_body(_PROJECT_SQL, proc)
        assert "LEFT JOIN dbo.[Customer] c ON c.[Id] = p.[CustomerId]" in body, proc
        assert "INNER JOIN dbo.[Customer]" not in body, proc


def test_by_id_procs_stay_free_of_customer_join():
    for proc in _BY_ID_PROCS:
        body = sproc_body(_PROJECT_SQL, proc)
        assert "CustomerName" not in body, proc
        assert "dbo.[Customer]" not in body, proc


def test_read_projects_qualifies_every_select_column_against_join():
    """Joining dbo.Customer makes bare Id/Name/RowVersion/... ambiguous, so every
    projected column in ReadProjects must be qualified with its table alias."""
    select_list = _select_lists("ReadProjects")[0]
    for line in (ln.strip().rstrip(",") for ln in select_list.splitlines() if ln.strip()):
        assert line.startswith(("p.[", "CONVERT(VARCHAR(19), p.[", "c.[")), line
    assert "ORDER BY p.[Name] ASC" in sproc_body(_PROJECT_SQL, "ReadProjects")


@patch("entities.project.persistence.repo.get_connection")
def test_read_all_returns_project_with_null_customer_when_join_misses(mock_get_connection):
    row = _mock_row(CustomerId=None, CustomerName=None)
    _setup_mock_connection(mock_get_connection, fetchall=[row])

    projects = ProjectRepository().read_all(actor_user_id=17, actor_is_system_admin=True)

    assert len(projects) == 1
    assert projects[0].customer_id is None
    assert projects[0].customer_name is None
