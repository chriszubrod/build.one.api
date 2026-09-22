"""U-500a: pin filtered UNIQUE on dbo.Company.RealmId alone.

Realm-scoping on the QBO expense-recode path is the only boundary today; this
DDL makes that provably Company-scoping (see comment block in dbo.company.sql).
"""
import re
from pathlib import Path

import pytest

from tests.sproc_text import REPO_ROOT, strip_sql_comments

COMPANY_SQL = REPO_ROOT / "entities" / "company" / "sql" / "dbo.company.sql"

# T1: RealmId appears many times in dbo.company.sql (sprocs, filters, comments);
# a naive `"[RealmId]" in body` pin is vacuous — count is 65 after U-500a.
REALM_ID_LITERAL_COUNT_IN_COMPANY_SQL = 65

_UQ_REALM_ID_BLOCK_RE = re.compile(
    r"IF OBJECT_ID\('dbo\.Company', 'U'\) IS NOT NULL AND NOT EXISTS \(\s*"
    r"SELECT 1 FROM sys\.indexes WHERE name = 'UQ_Company_RealmId'.*?"
    r"CREATE UNIQUE INDEX UQ_Company_RealmId ON \[dbo\]\.\[Company\] \(\[RealmId\]\)"
    r" WHERE \[RealmId\] IS NOT NULL;\s*"
    r"END\s*",
    re.DOTALL | re.IGNORECASE,
)

_UQ_QBO_REALM_BLOCK_SNIPPET = (
    "CREATE UNIQUE INDEX UQ_Company_QboId_RealmId ON [dbo].[Company] "
    "([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;"
)


def _company_sql_text() -> str:
    return COMPANY_SQL.read_text()


_REALM_UNIQUE_CREATE_RE = re.compile(
    r"CREATE UNIQUE INDEX UQ_Company_RealmId ON \[dbo\]\.\[Company\] \(\[RealmId\]\)"
    r"(?: WHERE \[RealmId\] IS NOT NULL)?;",
    re.IGNORECASE,
)


def _u500a_realm_unique_ddl_line(sql: str) -> str:
    """Comment-stripped CREATE line for UQ_Company_RealmId (not UQ_Company_QboId_RealmId)."""
    stripped = strip_sql_comments(sql)
    match = _REALM_UNIQUE_CREATE_RE.search(stripped)
    assert match is not None, "UQ_Company_RealmId single-column CREATE not found"
    return match.group(0)


def test_t1_realm_id_unique_index_keyed_on_realm_id_alone():
    raw = _company_sql_text()
    assert raw.count("RealmId") == REALM_ID_LITERAL_COUNT_IN_COMPANY_SQL

    ddl = _u500a_realm_unique_ddl_line(raw)
    assert "([RealmId])" in ddl
    assert "([QboId]" not in ddl


def test_t2_realm_id_unique_index_is_null_filtered():
    raw = _company_sql_text()
    ddl = _u500a_realm_unique_ddl_line(raw)
    assert "WHERE [RealmId] IS NOT NULL" in ddl


def test_t3_realm_id_unique_index_is_idempotent():
    raw = _company_sql_text()
    match = _UQ_REALM_ID_BLOCK_RE.search(strip_sql_comments(raw))
    assert match is not None, (
        "UQ_Company_RealmId must be wrapped in IF NOT EXISTS (sys.indexes ...) guard"
    )


def test_t4_qbo_id_realm_id_composite_unique_index_unchanged():
    raw = _company_sql_text()
    stripped = strip_sql_comments(raw)
    assert _UQ_QBO_REALM_BLOCK_SNIPPET in stripped
    assert (
        stripped.count("UQ_Company_QboId_RealmId")
        >= 2
    ), "index name must appear in guard and CREATE"


def _mutated_fails_tests(mutated: str, test_name: str) -> None:
    """Apply in-memory mutation and assert the named pin would fail."""
    if test_name == "test_t1_realm_id_unique_index_keyed_on_realm_id_alone":
        with pytest.raises(AssertionError):
            _u500a_realm_unique_ddl_line(mutated)
    elif test_name == "test_t2_realm_id_unique_index_is_null_filtered":
        with pytest.raises(AssertionError):
            ddl = _u500a_realm_unique_ddl_line(mutated)
            assert "WHERE [RealmId] IS NOT NULL" in ddl
    elif test_name == "test_t3_realm_id_unique_index_is_idempotent":
        assert _UQ_REALM_ID_BLOCK_RE.search(strip_sql_comments(mutated)) is None
    elif test_name == "test_t4_qbo_id_realm_id_composite_unique_index_unchanged":
        assert _UQ_QBO_REALM_BLOCK_SNIPPET not in strip_sql_comments(mutated)
    else:
        raise ValueError(test_name)


def test_mutation_t1_composite_key_must_not_satisfy_t1():
    original = _company_sql_text()
    mutated = original.replace(
        "CREATE UNIQUE INDEX UQ_Company_RealmId ON [dbo].[Company] ([RealmId]) WHERE [RealmId] IS NOT NULL;",
        "CREATE UNIQUE INDEX UQ_Company_RealmId ON [dbo].[Company] ([QboId], [RealmId]) WHERE [RealmId] IS NOT NULL;",
        1,
    )
    assert mutated != original
    _mutated_fails_tests(mutated, "test_t1_realm_id_unique_index_keyed_on_realm_id_alone")


def test_mutation_t2_drop_null_filter_must_fail_t2():
    original = _company_sql_text()
    mutated = original.replace(
        "CREATE UNIQUE INDEX UQ_Company_RealmId ON [dbo].[Company] ([RealmId]) WHERE [RealmId] IS NOT NULL;",
        "CREATE UNIQUE INDEX UQ_Company_RealmId ON [dbo].[Company] ([RealmId]);",
        1,
    )
    assert mutated != original
    _mutated_fails_tests(mutated, "test_t2_realm_id_unique_index_is_null_filtered")


def test_mutation_t3_bare_create_must_fail_t3():
    original = _company_sql_text()
    bare_create = (
        "CREATE UNIQUE INDEX UQ_Company_RealmId ON [dbo].[Company] ([RealmId]) "
        "WHERE [RealmId] IS NOT NULL;\nGO"
    )
    mutated = re.sub(
        r"IF OBJECT_ID\('dbo\.Company', 'U'\) IS NOT NULL AND NOT EXISTS \(\s*"
        r"SELECT 1 FROM sys\.indexes WHERE name = 'UQ_Company_RealmId'.*?"
        r"END\s*\nGO",
        bare_create,
        original,
        count=1,
        flags=re.DOTALL,
    )
    assert mutated != original
    _mutated_fails_tests(mutated, "test_t3_realm_id_unique_index_is_idempotent")


def test_mutation_t4_delete_composite_unique_must_fail_t4():
    original = _company_sql_text()
    mutated = re.sub(
        r"IF OBJECT_ID\('dbo\.Company', 'U'\) IS NOT NULL AND NOT EXISTS \(\s*"
        r"SELECT 1 FROM sys\.indexes WHERE name = 'UQ_Company_QboId_RealmId'.*?"
        r"END\s*\nGO\s*\n",
        "",
        original,
        count=1,
        flags=re.DOTALL,
    )
    assert mutated != original
    _mutated_fails_tests(mutated, "test_t4_qbo_id_realm_id_composite_unique_index_unchanged")
