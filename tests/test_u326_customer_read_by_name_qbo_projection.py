"""Pure-logic test for U-326: `ReadCustomerByName`'s SELECT list now projects
`[QboId]`/`[RealmId]` (`entities/customer/sql/dbo.customer.sql`), matching
`ReadCustomerById`'s shape. Before this fix, `CustomerCustomerConnector.
_resolve_customer_candidate`'s duplicate-QboId guard was provably dead against
a real DB read via `read_by_name` (booked in TODO.md by U-310) -- only the
`read_by_id`-based re-read in `_stamp_customer_identity` ever actually carried
identity. `CustomerRepository._from_db` already `getattr`s `QboId`/`RealmId`
(proven by the existing `ReadCustomerById`/`ReadCustomerByQboIdAndRealmId`
paths), so this test exercises the repo layer directly -- no live DB, per the
pure-logic harness -- to prove `read_by_name` now surfaces identity end to end
once the row carries those columns, and still degrades safely when it doesn't."""
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.customer.persistence.repo import CustomerRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
_CUSTOMER_SQL = REPO_ROOT / "entities" / "customer" / "sql" / "dbo.customer.sql"

_COL_TOKEN = re.compile(r"\[(\w+)\]")


def _proc_body(proc_name: str) -> str:
    """Static (no-DB) isolation of one named CREATE PROCEDURE batch's body in
    dbo.customer.sql, up to its closing `END;` line."""
    text = _CUSTOMER_SQL.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+(?:\[?dbo\]?\s*\.\s*)?\[?{proc_name}\b\]?"
        rf"(.*?)^END;",
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(text)
    assert match, f"could not isolate {proc_name}'s body in {_CUSTOMER_SQL}"
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
        "Id": 42,
        "PublicId": "00000000-0000-0000-0000-000000000042",
        "RowVersion": b"\x00\x00\x00\x00\x00\x00\x00\x01",
        "CreatedDatetime": "2026-01-01 00:00:00",
        "ModifiedDatetime": "2026-01-02 00:00:00",
        "Name": "Acme Construction",
        "Email": "billing@acme.test",
        "Phone": "555-0100",
        "QboId": "QBO-C-42",
        "RealmId": "realm-1",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _setup_mock_connection(mock_get_connection, row):
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_get_connection.return_value.__enter__.return_value = conn
    return cursor


def test_read_customer_by_name_select_shape_matches_read_by_id():
    """Static (no-DB) regression guard for the SQL change itself: fails
    red on the pre-fix file (ReadCustomerByName lacked QboId/RealmId) and
    green post-fix -- the mocked-row tests above only prove the Python
    read-through path, which was already correct; this proves the base
    SQL file's SELECT list actually changed, matching the task's own
    acceptance criterion ("match ReadCustomerById's SELECT shape exactly")."""
    by_id_columns = _select_columns("ReadCustomerById")
    by_name_columns = _select_columns("ReadCustomerByName")

    assert {"QboId", "RealmId"} <= by_name_columns
    assert by_name_columns == by_id_columns


@patch("entities.customer.persistence.repo.get_connection")
def test_read_by_name_surfaces_qbo_identity_when_set(mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection, _mock_row())

    customer = CustomerRepository().read_by_name("Acme Construction")

    assert customer.qbo_id == "QBO-C-42"
    assert customer.realm_id == "realm-1"
    assert customer.name == "Acme Construction"
    executed_sql = cursor.execute.call_args[0][0]
    assert "ReadCustomerByName" in executed_sql


@patch("entities.customer.persistence.repo.get_connection")
def test_read_by_name_defaults_identity_to_none_when_row_lacks_it(mock_get_connection):
    """A row shaped like the PRE-fix sproc (no QboId/RealmId attributes at
    all) must not raise -- `_from_db`'s `getattr(row, "QboId", None)` default
    is the safety net this fix relies on, not a new behavior."""
    row = _mock_row()
    del row.QboId
    del row.RealmId
    _setup_mock_connection(mock_get_connection, row)

    customer = CustomerRepository().read_by_name("Acme Construction")

    assert customer.qbo_id is None
    assert customer.realm_id is None
