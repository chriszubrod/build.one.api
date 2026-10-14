"""U-655: BudgetCheck / BudgetRemaining round-trip on bill coding suggestion sprocs."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}


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

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BILL_CODING_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_coding_suggestion.sql"
CODING_SUGGESTION_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"


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


def _exec_batches(cur, path: Path) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        cur.execute(batch)


def _drop_all(cur) -> None:
    cur.execute(
        "IF OBJECT_ID('dbo.BillDominantVoteForVendor', 'IF') IS NOT NULL "
        "DROP FUNCTION dbo.BillDominantVoteForVendor"
    )
    for proc in (
        "dbo.ReadCodingSuggestionByBillId",
        "dbo.UpsertCodingSuggestionForBill",
        "dbo.ReadVendorDominantProjectForBills",
        "dbo.ReadVendorDominantSubCostCodeForBills",
    ):
        cur.execute(f"IF OBJECT_ID('{proc}', 'P') IS NOT NULL DROP PROCEDURE {proc}")
    for tbl in (
        "dbo.CodingSuggestion",
        "dbo.BillLineItem",
        "dbo.Bill",
        "dbo.SubCostCode",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_minimal_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.SubCostCode (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            Number NVARCHAR(20) NOT NULL,
            Name NVARCHAR(255) NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.Bill (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            VendorId BIGINT NOT NULL,
            Status NVARCHAR(20) NOT NULL DEFAULT 'draft',
            CreatedDatetime DATETIME2(3) NOT NULL,
            IsDraft AS (CASE WHEN Status = 'completed' THEN CAST(0 AS BIT) ELSE CAST(1 AS BIT) END) PERSISTED
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BillId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            ProjectId BIGINT NULL,
            Amount DECIMAL(18,2) NULL
        );
        """
    )
    _exec_batches(cur, CODING_SUGGESTION_SQL)
    _exec_batches(cur, BILL_CODING_SQL)


@pytest.fixture
def conn():
    import pyodbc

    c = pyodbc.connect(os.environ["BUILDONE_TEST_SQLSERVER"], autocommit=True)
    cur = c.cursor()
    _drop_all(cur)
    _install_minimal_schema(cur)
    yield c
    _drop_all(cur)
    c.close()


def _insert_bill(cur) -> int:
    cur.execute(
        """
        INSERT INTO dbo.Bill (VendorId, Status, CreatedDatetime)
        OUTPUT INSERTED.Id
        VALUES (1, 'completed', '2026-01-01T10:00:00')
        """
    )
    return int(cur.fetchone()[0])


def test_upsert_read_round_trips_budget_fields(conn):
    cur = conn.cursor()
    bill_id = _insert_bill(cur)
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @SuggestedSubCostCodeId = 1,
            @SuggestionSource = 'vendor_history',
            @BudgetCheck = 'fits',
            @BudgetRemaining = 123.45
        """,
        bill_id,
    )
    row = cur.fetchone()
    assert row.BudgetCheck == "fits"
    assert float(row.BudgetRemaining) == pytest.approx(123.45)

    cur.execute("EXEC dbo.ReadCodingSuggestionByBillId @BillId = ?", bill_id)
    read_row = cur.fetchone()
    assert read_row.BudgetCheck == "fits"
    assert float(read_row.BudgetRemaining) == pytest.approx(123.45)


def test_replace_clears_budget_fields(conn):
    cur = conn.cursor()
    bill_id = _insert_bill(cur)
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @SuggestedSubCostCodeId = 1,
            @SuggestionSource = 'vendor_history',
            @BudgetCheck = 'over',
            @BudgetRemaining = -10.00
        """,
        bill_id,
    )
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @ReplaceSuggestion = 1,
            @SuggestedSubCostCodeId = 2,
            @SuggestionSource = 'memo',
            @BudgetCheck = NULL,
            @BudgetRemaining = NULL
        """,
        bill_id,
    )
    cur.execute(
        """
        SELECT BudgetCheck, BudgetRemaining, SuggestedSubCostCodeId
        FROM dbo.CodingSuggestion WHERE BillId = ?
        """,
        bill_id,
    )
    row = cur.fetchone()
    assert row.BudgetCheck is None
    assert row.BudgetRemaining is None
    assert int(row.SuggestedSubCostCodeId) == 2


def test_partial_update_preserves_budget_when_not_passed(conn):
    cur = conn.cursor()
    bill_id = _insert_bill(cur)
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @SuggestedSubCostCodeId = 1,
            @SuggestionSource = 'vendor_history',
            @BudgetCheck = 'tight',
            @BudgetRemaining = 50.00
        """,
        bill_id,
    )
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @SuggestionReason = 'updated reason only'
        """,
        bill_id,
    )
    cur.execute(
        "SELECT BudgetCheck, BudgetRemaining, SuggestionReason FROM dbo.CodingSuggestion WHERE BillId = ?",
        bill_id,
    )
    row = cur.fetchone()
    assert row.BudgetCheck == "tight"
    assert float(row.BudgetRemaining) == pytest.approx(50.0)
    assert row.SuggestionReason == "updated reason only"
