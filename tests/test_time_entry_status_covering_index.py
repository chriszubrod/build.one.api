"""Pure-logic guard: TimeEntryStatus covering index is declared in the base SQL file."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TIME_ENTRY_BASE = REPO_ROOT / "entities" / "time_entry" / "sql" / "dbo.time_entry.sql"

_INDEX_NAME = "IX_TimeEntryStatus_TimeEntryId_CreatedDatetime_Id"

_IDEMPOTENT_GUARD = re.compile(
    rf"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+sys\.indexes\s+WHERE\s+name\s*=\s*'{_INDEX_NAME}'",
    re.IGNORECASE | re.DOTALL,
)

_CREATE_INDEX = re.compile(
    rf"CREATE\s+INDEX\s+{_INDEX_NAME}\s+ON\s+\[dbo\]\.\[TimeEntryStatus\]\s*"
    rf"\(\s*\[TimeEntryId\]\s*,\s*\[CreatedDatetime\]\s*,\s*\[Id\]\s*\)\s*"
    rf"INCLUDE\s*\(\s*\[Status\]\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def _base_sql_text() -> str:
    return TIME_ENTRY_BASE.read_text(encoding="utf-8")


def test_covering_index_name_declared_in_base_file():
    assert _INDEX_NAME in _base_sql_text()


def test_covering_index_key_columns_in_order():
    assert _CREATE_INDEX.search(_base_sql_text()) is not None


def test_covering_index_includes_status():
    text = _base_sql_text()
    match = _CREATE_INDEX.search(text)
    assert match is not None
    assert re.search(r"INCLUDE\s*\(\s*\[Status\]\s*\)", match.group(0), re.IGNORECASE)


def test_covering_index_wrapped_in_idempotent_guard():
    assert _IDEMPOTENT_GUARD.search(_base_sql_text()) is not None
