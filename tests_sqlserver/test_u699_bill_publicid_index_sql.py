"""U-699: dbo.bill_publicid_index.sql idempotency and uniqueness on test SQL Server."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pyodbc
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}
REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_SQL = REPO_ROOT / "entities" / "bill" / "sql" / "dbo.bill_publicid_index.sql"
INDEX_NAME = "UQ_Bill_PublicId"
TEST_SCHEMA = "u699test"
SCRATCH_TABLE = f"{TEST_SCHEMA}.Bill"


def _server_host(conn_str: str) -> str:
    for part in conn_str.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() == "SERVER":
            host = re.sub(r"^tcp:", "", value.strip().strip("{}"), flags=re.IGNORECASE)
            return host.split(",")[0].strip().lower()
    return ""


if os.environ.get("BUILDONE_TEST_SQLSERVER") and _server_host(
    os.environ["BUILDONE_TEST_SQLSERVER"]
) not in _LOCAL_SERVERS:
    raise RuntimeError(
        "tests_sqlserver refuses to run: SERVER in BUILDONE_TEST_SQLSERVER must be 127.0.0.1 or localhost"
    )


def _batches(sql: str) -> list[str]:
    out, cur = [], []
    for line in sql.splitlines():
        if line.strip() == "GO":
            out.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    out.append("\n".join(cur))
    return [b for b in out if any(l.strip() and not l.strip().startswith("--") for l in b.splitlines())]


def _scratch_index_sql() -> str:
    text = INDEX_SQL.read_text(encoding="utf-8")
    assert INDEX_NAME in text
    return (
        text.replace("[dbo].[Bill]", f"[{TEST_SCHEMA}].[Bill]")
        .replace("OBJECT_ID('dbo.Bill'", f"OBJECT_ID('{SCRATCH_TABLE}'")
    )


def _drop_scratch_index(cur) -> None:
    cur.execute(
        f"""
        IF EXISTS (
            SELECT 1 FROM sys.indexes
            WHERE name = '{INDEX_NAME}' AND object_id = OBJECT_ID('{SCRATCH_TABLE}')
        )
        DROP INDEX [{INDEX_NAME}] ON [{TEST_SCHEMA}].[Bill];
        """
    )


def _drop_scratch_table(cur) -> None:
    cur.execute(
        f"""
        IF OBJECT_ID('{SCRATCH_TABLE}', 'U') IS NOT NULL
            DROP TABLE [{TEST_SCHEMA}].[Bill];
        """
    )


def _drop_scratch_schema(cur) -> None:
    cur.execute(
        f"""
        IF EXISTS (SELECT 1 FROM sys.schemas WHERE name = '{TEST_SCHEMA}')
            DROP SCHEMA [{TEST_SCHEMA}];
        """
    )


def _ensure_scratch_table(cur) -> None:
    cur.execute(f"IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = '{TEST_SCHEMA}') EXEC('CREATE SCHEMA [{TEST_SCHEMA}]');")
    cur.execute(
        f"""
        IF OBJECT_ID('{SCRATCH_TABLE}', 'U') IS NULL
        CREATE TABLE [{TEST_SCHEMA}].[Bill] (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID()
        );
        """
    )


def _index_row(cur):
    cur.execute(
        f"""
        SELECT i.name, i.is_unique
        FROM sys.indexes i
        WHERE i.object_id = OBJECT_ID('{SCRATCH_TABLE}')
          AND i.name = ?
        """,
        INDEX_NAME,
    )
    return cur.fetchone()


def _maybe_drop_legacy_stub_dbo_bill(cur) -> str | None:
    """One-off safe cleanup: stub dbo.Bill from an earlier test run (Id + PublicId only, 0 rows)."""
    cur.execute(
        """
        IF OBJECT_ID('dbo.Bill', 'U') IS NULL
            SELECT 0 AS drop_ok, 0 AS col_count, 0 AS row_count;
        ELSE
        BEGIN
            DECLARE @cols INT = (
                SELECT COUNT(*) FROM sys.columns c
                WHERE c.object_id = OBJECT_ID('dbo.Bill')
            );
            DECLARE @rows BIGINT = (SELECT COUNT(*) FROM dbo.Bill);
            SELECT CASE WHEN @cols = 2 AND @rows = 0 THEN 1 ELSE 0 END AS drop_ok,
                   @cols AS col_count, @rows AS row_count;
        END
        """
    )
    row = cur.fetchone()
    if not row or not row.drop_ok:
        return None
    cur.execute(
        """
        DECLARE @idx SYSNAME, @sql NVARCHAR(4000);
        DECLARE idx CURSOR LOCAL FAST_FORWARD FOR
            SELECT i.name
            FROM sys.indexes i
            WHERE i.object_id = OBJECT_ID('dbo.Bill') AND i.name IS NOT NULL AND i.is_primary_key = 0;
        OPEN idx;
        FETCH NEXT FROM idx INTO @idx;
        WHILE @@FETCH_STATUS = 0
        BEGIN
            SET @sql = N'DROP INDEX [' + @idx + N'] ON [dbo].[Bill];';
            EXEC sp_executesql @sql;
            FETCH NEXT FROM idx INTO @idx;
        END
        CLOSE idx; DEALLOCATE idx;
        DROP TABLE dbo.Bill;
        """
    )
    return "dbo.Bill (2 columns, 0 rows)"


@pytest.fixture()
def conn():
    c = pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=False)
    try:
        yield c
    finally:
        c.close()


def test_bill_publicid_index_idempotent_and_unique(conn):
    cur = conn.cursor()
    dropped_legacy = None
    try:
        dropped_legacy = _maybe_drop_legacy_stub_dbo_bill(cur)
        _drop_scratch_index(cur)
        _drop_scratch_table(cur)
        _drop_scratch_schema(cur)
        conn.commit()

        _ensure_scratch_table(cur)
        conn.commit()

        scratch_sql = _scratch_index_sql()
        for _ in range(2):
            for batch in _batches(scratch_sql):
                cur.execute(batch)
            conn.commit()

        row = _index_row(cur)
        assert row is not None
        assert row.name == INDEX_NAME
        assert row.is_unique is True

        pid = uuid.uuid4()
        cur.execute(f"INSERT INTO [{TEST_SCHEMA}].[Bill] (PublicId) VALUES (?)", pid)
        conn.commit()
        with pytest.raises(pyodbc.Error):
            cur.execute(f"INSERT INTO [{TEST_SCHEMA}].[Bill] (PublicId) VALUES (?)", pid)
            conn.commit()

        cur.execute(f"DELETE FROM [{TEST_SCHEMA}].[Bill] WHERE PublicId = ?", pid)
        conn.commit()
    finally:
        try:
            _drop_scratch_index(cur)
            _drop_scratch_table(cur)
            _drop_scratch_schema(cur)
            conn.commit()
        except Exception:
            conn.rollback()

    # dropped_legacy is reported by the session builder (one-off local test DB cleanup).
    assert dropped_legacy is None or isinstance(dropped_legacy, str)
