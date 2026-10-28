"""U-701a: apply real entity SQL table definitions on local test SQL Server only."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator

import pyodbc
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_DDL_ONLY: frozenset[Path] = frozenset(
    {
        REPO_ROOT / "entities/attachment/sql/dbo.attachment.sql",
        REPO_ROOT / "entities/taxpayer/sql/dbo.taxpayer.sql",
        REPO_ROOT / "entities/taxpayer_attachment/sql/dbo.taxpayer_attachment.sql",
        REPO_ROOT / "entities/cost_code/sql/dbo.costcode.sql",
        REPO_ROOT / "entities/sub_cost_code/sql/dbo.subcostcode.sql",
        REPO_ROOT / "entities/project/sql/dbo.project.sql",
        REPO_ROOT / "entities/bill/sql/dbo.bill.sql",
        REPO_ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql",
        REPO_ROOT / "entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql",
    }
)

_ATTACHMENT_MIGRATIONS: tuple[Path, ...] = (
    REPO_ROOT / "entities/attachment/sql/add_extraction_fields.sql",
    REPO_ROOT / "entities/attachment/sql/add_categorization_fields.sql",
)

SQL_FILES_IN_ORDER: tuple[Path, ...] = (
    REPO_ROOT / "entities/attachment/sql/dbo.attachment.sql",
    REPO_ROOT / "entities/taxpayer/sql/dbo.taxpayer.sql",
    REPO_ROOT / "entities/taxpayer_attachment/sql/dbo.taxpayer_attachment.sql",
    REPO_ROOT / "entities/cost_code/sql/dbo.costcode.sql",
    REPO_ROOT / "entities/sub_cost_code/sql/dbo.subcostcode.sql",
    REPO_ROOT / "entities/project/sql/dbo.project.sql",
    REPO_ROOT / "entities/bill/sql/dbo.bill.sql",
    REPO_ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql",
    REPO_ROOT / "entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql",
    REPO_ROOT / "entities/bill_intake_prediction/sql/dbo.bill_intake_prediction.sql",
    REPO_ROOT / "entities/attachment/sql/dbo.attachment_text_token_hash.sql",
    REPO_ROOT / "entities/coding_suggestion/sql/dbo.similar_bills_corpus.sql",
)

_SIMILAR_BILLS_CORPUS_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.similar_bills_corpus.sql"

# Phase 5 tenant columns (scripts/migrations/phase5_company_id_*.sql) — absent from
# entity base CREATE TABLE blocks but present on prod.
_PHASE5_COMPANY_ID_TABLES: tuple[str, ...] = (
    "Project",
    "Bill",
    "BillLineItem",
    "Attachment",
    "BillLineItemAttachment",
)


_U701A_TABLES_DROP_ORDER: tuple[str, ...] = (
    "dbo.AttachmentTextTokenHash",
    "dbo.BillIntakePrediction",
    "dbo.BillLineItemAttachment",
    "dbo.BillLineItem",
    "dbo.TaxpayerAttachment",
    "dbo.Bill",
    "dbo.Attachment",
    "dbo.SubCostCode",
    "dbo.CostCode",
    "dbo.Project",
    "dbo.Taxpayer",
)


def _server_host(conn_str: str) -> str:
    for part in conn_str.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() == "SERVER":
            host = re.sub(r"^tcp:", "", value.strip().strip("{}"), flags=re.IGNORECASE)
            return host.split(",")[0].strip().lower()
    return ""


def assert_local_test_sql_server() -> None:
    conn_str = os.environ.get("BUILDONE_TEST_SQLSERVER", "")
    if not conn_str:
        return
    if _server_host(conn_str) not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("U-701a SQL fixtures refuse non-local BUILDONE_TEST_SQLSERVER")


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


def _drain_cursor(cur) -> None:
    while True:
        try:
            if cur.description:
                cur.fetchall()
        except pyodbc.Error:
            return
        try:
            more = cur.nextset()
        except pyodbc.Error:
            return
        if not more:
            break


def exec_sql_file(cur, path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for batch in _batches(text):
        cur.execute(batch)
        _drain_cursor(cur)


def exec_sql_file_split_before_procedures(cur, path: Path) -> None:
    """Run DDL/ALTER batches, then (optionally) a hook, then sproc batches."""
    text = path.read_text(encoding="utf-8")
    pre: list[str] = []
    post: list[str] = []
    for batch in _batches(text):
        upper = batch.upper()
        if "CREATE OR ALTER PROCEDURE" in upper or "CREATE PROCEDURE" in upper:
            post.append(batch)
        else:
            pre.append(batch)
    for batch in pre:
        cur.execute(batch)
    return post


def exec_table_ddl_only(cur, path: Path, *, extra_migrations: tuple[Path, ...] = ()) -> None:
    """Apply CREATE/ALTER batches only — skip sprocs/views that assume full prod column set."""
    text = path.read_text(encoding="utf-8")
    for batch in _batches(text):
        upper = batch.upper()
        if "CREATE OR ALTER PROCEDURE" in upper or "CREATE PROCEDURE" in upper:
            continue
        if "CREATE VIEW" in upper or "CREATE OR ALTER VIEW" in upper:
            continue
        try:
            cur.execute(batch)
            _drain_cursor(cur)
        except pyodbc.ProgrammingError as error:
            # Shared test DBs may carry stub tables; skip optional indexes/constraints
            # until additive ALTER batches from the same file have run.
            if error.args[0] == "42S22" and "CREATE TABLE" not in upper:
                continue
            raise
    for mig in extra_migrations:
        exec_sql_file(cur, mig)


def _ensure_reference_tables(cur) -> None:
    """Minimal parents so real entity SQL can add FKs (User id=17 is the project default)."""
    cur.execute(
        """
        IF OBJECT_ID('dbo.[User]', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.[User] (
                [Id] BIGINT NOT NULL PRIMARY KEY,
                [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
                [RowVersion] ROWVERSION NOT NULL,
                [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
                [ModifiedDatetime] DATETIME2(3) NULL
            );
        END;
        IF NOT EXISTS (SELECT 1 FROM dbo.[User] WHERE [Id] = 17)
            INSERT dbo.[User]([Id]) VALUES (17);

        IF OBJECT_ID('dbo.Vendor', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.[Vendor] (
                [Id] BIGINT NOT NULL PRIMARY KEY,
                [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
                [RowVersion] ROWVERSION NOT NULL,
                [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
                [ModifiedDatetime] DATETIME2(3) NULL,
                [Name] NVARCHAR(255) NOT NULL DEFAULT N'test'
            );
        END;
        IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE [Id] = 1)
            INSERT dbo.[Vendor]([Id], [Name]) VALUES (1, N'test-vendor');

        IF OBJECT_ID('dbo.Customer', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.[Customer] (
                [Id] BIGINT NOT NULL PRIMARY KEY,
                [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
                [RowVersion] ROWVERSION NOT NULL,
                [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
                [ModifiedDatetime] DATETIME2(3) NULL,
                [Name] NVARCHAR(255) NOT NULL DEFAULT N'test'
            );
        END;

        IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.[PaymentTerm] (
                [Id] BIGINT NOT NULL PRIMARY KEY,
                [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
                [RowVersion] ROWVERSION NOT NULL,
                [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
                [ModifiedDatetime] DATETIME2(3) NULL,
                [Name] NVARCHAR(255) NOT NULL DEFAULT N'Net 30'
            );
        END;

        IF OBJECT_ID('dbo.Taxpayer', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.[Taxpayer] (
                [Id] BIGINT NOT NULL PRIMARY KEY,
                [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
                [RowVersion] ROWVERSION NOT NULL,
                [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
                [ModifiedDatetime] DATETIME2(3) NULL,
                [Name] NVARCHAR(255) NOT NULL DEFAULT N'test'
            );
        END;
        IF NOT EXISTS (SELECT 1 FROM dbo.[Taxpayer] WHERE [Id] = 1)
            INSERT dbo.[Taxpayer]([Id], [Name]) VALUES (1, N'test-taxpayer');
        """
    )


def drop_u701a_tables(cur) -> None:
    for name in _U701A_TABLES_DROP_ORDER:
        cur.execute(
            f"""
            IF OBJECT_ID('{name}', 'U') IS NOT NULL
            BEGIN
                DECLARE @sv BIT = 0;
                IF EXISTS (
                    SELECT 1 FROM sys.tables t
                    INNER JOIN sys.schemas s ON s.schema_id = t.schema_id
                    WHERE t.name = PARSENAME('{name}', 1)
                      AND s.name = PARSENAME('{name}', 2)
                      AND t.temporal_type = 2
                )
                    SET @sv = 1;
                IF @sv = 1
                    ALTER TABLE {name} SET (SYSTEM_VERSIONING = OFF);
            END;
            IF OBJECT_ID('{name}', 'U') IS NOT NULL DROP TABLE {name};
            """
        )


def _ensure_company_stub(cur) -> None:
    """Company id=1 matches phase-5 DEFAULT (1) / backfill target."""
    cur.execute(
        """
        IF OBJECT_ID('dbo.Company', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.[Company] (
                [Id] BIGINT NOT NULL PRIMARY KEY,
                [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
                [RowVersion] ROWVERSION NOT NULL,
                [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
                [ModifiedDatetime] DATETIME2(3) NULL,
                [Name] NVARCHAR(255) NOT NULL DEFAULT N'test-company'
            );
        END;
        IF NOT EXISTS (SELECT 1 FROM dbo.[Company] WHERE [Id] = 1)
            INSERT dbo.[Company]([Id], [Name]) VALUES (1, N'Rogers Build, Inc.');
        IF NOT EXISTS (SELECT 1 FROM dbo.[Company] WHERE [Id] = 2)
            INSERT dbo.[Company]([Id], [Name]) VALUES (2, N'other-company');
        """
    )


def _apply_phase5_company_id_columns(cur) -> None:
    """Mirror prod phase-5 CompanyId: BIGINT NOT NULL, DEFAULT (1), after repo DDL."""
    _ensure_company_stub(cur)
    for table in _PHASE5_COMPANY_ID_TABLES:
        df_name = f"DF_{table}_CompanyId"
        ix_name = f"IX_{table}_CompanyId"
        # One statement per execute — SQL Server compiles whole batches and rejects
        # UPDATE ... CompanyId before the ADD batch has run.
        cur.execute(
            f"""
            IF OBJECT_ID('dbo.[{table}]', 'U') IS NOT NULL
               AND COL_LENGTH('dbo.[{table}]', 'CompanyId') IS NULL
                ALTER TABLE dbo.[{table}] ADD [CompanyId] BIGINT NULL;
            """
        )
        cur.execute(
            f"""
            IF OBJECT_ID('dbo.[{table}]', 'U') IS NOT NULL
               AND COL_LENGTH('dbo.[{table}]', 'CompanyId') IS NOT NULL
                UPDATE dbo.[{table}] SET [CompanyId] = 1 WHERE [CompanyId] IS NULL;
            """
        )
        cur.execute(
            f"""
            IF OBJECT_ID('dbo.[{table}]', 'U') IS NOT NULL
               AND EXISTS (
                    SELECT 1 FROM sys.columns
                    WHERE object_id = OBJECT_ID('dbo.[{table}]')
                      AND name = 'CompanyId' AND is_nullable = 1
               )
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM sys.indexes
                    WHERE name = '{ix_name}' AND object_id = OBJECT_ID('dbo.[{table}]')
                )
                    DROP INDEX [{ix_name}] ON dbo.[{table}];
                ALTER TABLE dbo.[{table}] ALTER COLUMN [CompanyId] BIGINT NOT NULL;
                IF NOT EXISTS (
                    SELECT 1 FROM sys.indexes
                    WHERE name = '{ix_name}' AND object_id = OBJECT_ID('dbo.[{table}]')
                )
                    CREATE INDEX [{ix_name}] ON dbo.[{table}] ([CompanyId]);
            END;
            """
        )
        cur.execute(
            f"""
            IF OBJECT_ID('dbo.[{table}]', 'U') IS NOT NULL
               AND COL_LENGTH('dbo.[{table}]', 'CompanyId') IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = '{df_name}')
                ALTER TABLE dbo.[{table}] ADD CONSTRAINT [{df_name}] DEFAULT (1) FOR [CompanyId];
            """
        )


def _seed_reference_rows(cur) -> None:
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM dbo.[CostCode] WHERE [Id] = 1)
        BEGIN
            SET IDENTITY_INSERT dbo.[CostCode] ON;
            INSERT dbo.[CostCode]([Id], [CreatedDatetime], [Number], [Name])
            VALUES (1, SYSUTCDATETIME(), N'1', N'test-cc');
            SET IDENTITY_INSERT dbo.[CostCode] OFF;
        END;
        IF NOT EXISTS (SELECT 1 FROM dbo.[SubCostCode] WHERE [Id] = 1)
        BEGIN
            SET IDENTITY_INSERT dbo.[SubCostCode] ON;
            INSERT dbo.[SubCostCode]([Id], [CreatedDatetime], [Number], [Name], [CostCodeId])
            VALUES (1, SYSUTCDATETIME(), N'1', N'test-scc', 1);
            SET IDENTITY_INSERT dbo.[SubCostCode] OFF;
        END;
        IF NOT EXISTS (SELECT 1 FROM dbo.[Project] WHERE [Id] = 1)
        BEGIN
            SET IDENTITY_INSERT dbo.[Project] ON;
            INSERT dbo.[Project]([Id], [CreatedDatetime], [Name])
            VALUES (1, SYSUTCDATETIME(), N'test-project');
            SET IDENTITY_INSERT dbo.[Project] OFF;
        END;
        IF NOT EXISTS (SELECT 1 FROM dbo.[Taxpayer] WHERE [Id] = 1)
        BEGIN
            SET IDENTITY_INSERT dbo.[Taxpayer] ON;
            INSERT dbo.[Taxpayer]([Id], [CreatedDatetime], [Name])
            VALUES (1, SYSUTCDATETIME(), N'test-taxpayer');
            SET IDENTITY_INSERT dbo.[Taxpayer] OFF;
        END;
        """
    )


def _drop_stale_minimal_tables(cur) -> None:
    """Remove leftover stub tables from older tests_sqlserver runs (wrong column sets)."""
    cur.execute(
        """
        IF OBJECT_ID('dbo.CostCode', 'U') IS NOT NULL
           AND COL_LENGTH('dbo.CostCode', 'Description') IS NULL
            DROP TABLE dbo.[CostCode];
        IF OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL
           AND COL_LENGTH('dbo.Attachment', 'ExtractionStatus') IS NULL
            DROP TABLE dbo.[Attachment];
        IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL
           AND COL_LENGTH('dbo.Bill', 'Status') IS NULL
            DROP TABLE dbo.[Bill];
        IF EXISTS (
            SELECT 1 FROM sys.columns
            WHERE object_id = OBJECT_ID('dbo.Bill') AND name = 'IsDraft' AND is_computed = 1
        )
            DROP TABLE dbo.[Bill];
        IF OBJECT_ID('dbo.SubCostCode', 'U') IS NOT NULL
           AND COL_LENGTH('dbo.SubCostCode', 'CostCodeId') IS NULL
            DROP TABLE dbo.[SubCostCode];
        IF OBJECT_ID('dbo.Project', 'U') IS NOT NULL
           AND COL_LENGTH('dbo.Project', 'Status') IS NULL
            DROP TABLE dbo.[Project];
        """
    )


def install_u701a_real_schema(cur) -> None:
    """Drop U-701a tables, recreate from repo SQL (transaction-scoped; roll back after test)."""
    assert_local_test_sql_server()
    drop_u701a_tables(cur)
    _drop_stale_minimal_tables(cur)
    _ensure_reference_tables(cur)
    attachment_sql = REPO_ROOT / "entities/attachment/sql/dbo.attachment.sql"
    for path in SQL_FILES_IN_ORDER:
        if path in _DDL_ONLY:
            extra = _ATTACHMENT_MIGRATIONS if path == attachment_sql else ()
            exec_table_ddl_only(cur, path, extra_migrations=extra)
        else:
            if path == _SIMILAR_BILLS_CORPUS_SQL:
                _apply_phase5_company_id_columns(cur)
            exec_sql_file(cur, path)
    _seed_reference_rows(cur)


@pytest.fixture
def u701a_real_schema_conn() -> Iterator[pyodbc.Connection]:
    assert_local_test_sql_server()
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    conn = pyodbc.connect(conn_str, autocommit=False)
    cur = conn.cursor()
    try:
        install_u701a_real_schema(cur)
        _drain_cursor(cur)
        cur.close()
        yield conn
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT COL_LENGTH(?, ?)",
        (table, column),
    )
    row = cur.fetchone()
    return row is not None and row[0] is not None
