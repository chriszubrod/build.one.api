"""U-632: holding-code sproc gates on real SQL Server."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pyodbc
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)

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

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBCOSTCODE_SQL = REPO_ROOT / "entities/sub_cost_code/sql/dbo.subcostcode.sql"
BILL_SQL = REPO_ROOT / "entities/bill/sql/dbo.bill.sql"
BILL_LINE_ITEM_SQL = REPO_ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql"
EXPENSE_SQL = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"
EXPENSE_LINE_ITEM_SQL = (
    REPO_ROOT / "entities/expense_line_item/sql/dbo.expense_line_item.sql"
)
REVIEW_SQL = REPO_ROOT / "entities/review/sql/dbo.review.sql"
EXPENSE_CODING_SQL = REPO_ROOT / "entities/coding_suggestion/sql/expense_coding_suggestion.sql"
BILL_CODING_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_coding_suggestion.sql"
MIGRATION_SQL = REPO_ROOT / "scripts/migrations/u632_subcostcode_is_holding_code.sql"


def _sqlserver_connect(conn_str: str, *, autocommit: bool = False):
    return pyodbc.connect(conn_str, autocommit=autocommit)


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


def _exec_batches_matching(cur, path: Path, *needles: str) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        if any(n in batch for n in needles):
            cur.execute(batch)


def _drop_all(cur) -> None:
    for proc in (
        "dbo.FinalizeBillById",
        "dbo.TransitionBillStatus",
        "dbo.FinalizeExpenseById",
        "dbo.TransitionExpenseStatus",
        "dbo.CreateBillLineItem",
        "dbo.UpdateBillLineItemById",
        "dbo.CreateExpenseLineItem",
        "dbo.UpdateExpenseLineItemById",
        "dbo.CreateReview",
    ):
        cur.execute(f"IF OBJECT_ID('{proc}', 'P') IS NOT NULL DROP PROCEDURE {proc}")
    cur.execute("IF OBJECT_ID('dbo.vw_Review', 'V') IS NOT NULL DROP VIEW dbo.vw_Review")
    for tbl in (
        "dbo.ExpenseLineItem",
        "dbo.Expense",
        "dbo.BillLineItem",
        "dbo.Bill",
        "dbo.SubCostCode",
        "dbo.Vendor",
        "dbo.Attachment",
        "dbo.BillLineItemAttachment",
        "dbo.ExpenseLineItemAttachment",
        "dbo.BillCreditLineItemAttachment",
        "dbo.BillCreditLineItem",
        "dbo.BillCredit",
        "dbo.SubCostCodeLegacy",
        "dbo.Review",
        "dbo.ReviewStatus",
        "dbo.Invoice",
        "dbo.ContractLabor",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")
    cur.execute("IF OBJECT_ID('dbo.vw_SubCostCode', 'V') IS NOT NULL DROP VIEW dbo.vw_SubCostCode")


# Production column shapes (read from sys.columns 2026-10-15) for every table the
# real CreateBillLineItem / UpdateBillLineItemById / CreateExpenseLineItem /
# UpdateExpenseLineItemById / CreateReview batches reference. The minimal tables
# above only carry what the Finalize/Transition gates need; these are added as
# NULLable so the full sprocs compile and run (U-632 r2: the F3/G9 tests used to
# be skipped for want of them).
_PROD_COLUMNS = {
    "Bill": ["CompanyId BIGINT", "CreatedByUserId BIGINT", "SyncToken NVARCHAR(50)"],
    "BillLineItem": [
        "Quantity DECIMAL(18,4)", "Rate DECIMAL(18,4)", "CompanyId BIGINT",
        "CreatedByUserId BIGINT", "QboId NVARCHAR(50)", "RealmId NVARCHAR(50)",
        "CodingPrefillState NVARCHAR(12)",
    ],
    "Expense": [
        "SourceEmailMessageId BIGINT", "CompanyId BIGINT", "CreatedByUserId BIGINT",
        "SyncToken NVARCHAR(50)",
    ],
    "ExpenseLineItem": [
        "Quantity DECIMAL(18,4)", "Rate DECIMAL(18,4)", "CompanyId BIGINT",
        "CreatedByUserId BIGINT", "QboId NVARCHAR(50)", "RealmId NVARCHAR(50)",
        "BillableDecidedByUserId BIGINT", "BillableDecidedAt DATETIME2(3)",
    ],
    "BillCredit": [
        "RowVersion ROWVERSION", "CreatedDatetime DATETIME2(3)", "ModifiedDatetime DATETIME2(3)",
        "TotalAmount DECIMAL(18,2)", "Memo NVARCHAR(MAX)", "IsDraft BIT", "SourceEmailMessageId BIGINT",
        "CompanyId BIGINT", "CreatedByUserId BIGINT", "QboId NVARCHAR(50)", "RealmId NVARCHAR(50)",
    ],
}
_PROD_TABLES = {
    "Invoice": """CREATE TABLE dbo.Invoice (
        Id BIGINT IDENTITY(1,1) PRIMARY KEY, PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        RowVersion ROWVERSION NOT NULL, CreatedDatetime DATETIME2(3) NULL, ModifiedDatetime DATETIME2(3) NULL,
        ProjectId BIGINT NULL, PaymentTermId BIGINT NULL, InvoiceDate DATETIME2(3) NULL, DueDate DATETIME2(3) NULL,
        InvoiceNumber NVARCHAR(50) NULL, TotalAmount DECIMAL(18,2) NULL, Memo NVARCHAR(MAX) NULL,
        IsDraft BIT NOT NULL DEFAULT 1, CompanyId BIGINT NULL, CreatedByUserId BIGINT NULL,
        QboId NVARCHAR(50) NULL, RealmId NVARCHAR(50) NULL, SyncToken NVARCHAR(50) NULL)""",
    "ContractLabor": """CREATE TABLE dbo.ContractLabor (
        Id BIGINT IDENTITY(1,1) PRIMARY KEY, PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        RowVersion ROWVERSION NOT NULL, CreatedDatetime DATETIME2(3) NULL, ModifiedDatetime DATETIME2(3) NULL,
        VendorId BIGINT NULL, ProjectId BIGINT NULL, EmployeeName NVARCHAR(255) NULL, JobName NVARCHAR(255) NULL,
        WorkDate DATE NULL, TimeIn NVARCHAR(20) NULL, TimeOut NVARCHAR(20) NULL, BreakTime NVARCHAR(20) NULL,
        RegularHours DECIMAL(6,2) NULL, OvertimeHours DECIMAL(6,2) NULL, TotalHours DECIMAL(6,2) NULL,
        HourlyRate DECIMAL(18,4) NULL, Markup DECIMAL(18,4) NULL, TotalAmount DECIMAL(18,2) NULL,
        SubCostCodeId BIGINT NULL, Description NVARCHAR(MAX) NULL, BillingPeriodStart DATE NULL,
        Status NVARCHAR(20) NULL, BillLineItemId BIGINT NULL, BillVendorId BIGINT NULL, BillDate DATE NULL,
        DueDate DATE NULL, BillNumber NVARCHAR(50) NULL, ImportBatchId NVARCHAR(50) NULL,
        SourceFile NVARCHAR(255) NULL, SourceRow INT NULL, CompanyId BIGINT NULL, CreatedByUserId BIGINT NULL,
        SourceTimeEntryId BIGINT NULL)""",
}


def _ensure_prod_columns(cur) -> None:
    for table, ddl in _PROD_TABLES.items():
        cur.execute(f"IF OBJECT_ID('dbo.{table}', 'U') IS NULL EXEC('{ddl.replace(chr(39), chr(39) * 2)}')")
    for table, cols in _PROD_COLUMNS.items():
        for col in cols:
            name = col.split()[0]
            cur.execute(
                f"IF COL_LENGTH('dbo.{table}', '{name}') IS NULL ALTER TABLE dbo.{table} ADD {col}"
                + ("" if "ROWVERSION" in col else " NULL")
            )


def _install_minimal_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.SubCostCode (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            RowVersion ROWVERSION NOT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            Number NVARCHAR(50) NOT NULL,
            Name NVARCHAR(255) NOT NULL,
            Description NVARCHAR(255) NULL,
            CostCodeId BIGINT NOT NULL DEFAULT 1,
            Aliases NVARCHAR(500) NULL,
            QboActive BIT NULL,
            QboId NVARCHAR(50) NULL,
            RealmId NVARCHAR(50) NULL,
            IsHoldingCode BIT NOT NULL CONSTRAINT DF_SubCostCode_IsHoldingCode DEFAULT 0
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.Bill (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            RowVersion ROWVERSION NOT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            VendorId BIGINT NULL,
            PaymentTermId BIGINT NULL,
            BillDate DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            DueDate DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            BillNumber NVARCHAR(50) NULL,
            TotalAmount DECIMAL(18,2) NULL,
            Memo NVARCHAR(MAX) NULL,
            IsDraft BIT NOT NULL DEFAULT 1,
            Status NVARCHAR(20) NOT NULL DEFAULT 'draft',
            StatusDatetime DATETIME2(3) NULL,
            StatusOrigin NVARCHAR(24) NULL,
            StatusSourceRef NVARCHAR(64) NULL,
            IntakeSource NVARCHAR(20) NULL,
            IntakeSourceDetail NVARCHAR(100) NULL,
            SourceEmailMessageId BIGINT NULL,
            QboId NVARCHAR(50) NULL,
            RealmId NVARCHAR(50) NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            RowVersion ROWVERSION NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.Expense (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            RowVersion ROWVERSION NOT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            VendorId BIGINT NULL,
            ExpenseDate DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ReferenceNumber NVARCHAR(50) NULL,
            TotalAmount DECIMAL(18,2) NULL,
            Memo NVARCHAR(MAX) NULL,
            IsDraft BIT NOT NULL DEFAULT 1,
            Status NVARCHAR(20) NOT NULL DEFAULT 'draft',
            StatusDatetime DATETIME2(3) NULL,
            StatusOrigin NVARCHAR(24) NULL,
            StatusSourceRef NVARCHAR(64) NULL,
            IsCredit BIT NOT NULL DEFAULT 0,
            QboId NVARCHAR(50) NULL,
            RealmId NVARCHAR(50) NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.ExpenseLineItem (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            ExpenseId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            ProjectId BIGINT NULL,
            IsBillable BIT NOT NULL DEFAULT 1,
            IsBilled BIT NOT NULL DEFAULT 0,
            IsDraft BIT NOT NULL DEFAULT 0,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            RowVersion ROWVERSION NOT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            Description NVARCHAR(255) NULL,
            Amount DECIMAL(18,2) NULL,
            Markup DECIMAL(18,2) NULL,
            Price DECIMAL(18,2) NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.Vendor (
            Id BIGINT NOT NULL PRIMARY KEY,
            Name NVARCHAR(255) NULL
        );
        INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'Test Vendor');
        CREATE TABLE dbo.Attachment (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID()
        );
        CREATE TABLE dbo.BillLineItemAttachment (
            BillLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        CREATE TABLE dbo.ExpenseLineItemAttachment (
            ExpenseLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        CREATE TABLE dbo.BillCreditLineItem (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillCreditId BIGINT NOT NULL DEFAULT 1,
            SubCostCodeId BIGINT NULL,
            ProjectId BIGINT NULL,
            IsBillable BIT NOT NULL DEFAULT 1,
            IsBilled BIT NOT NULL DEFAULT 0,
            IsDraft BIT NOT NULL DEFAULT 0,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            Description NVARCHAR(255) NULL,
            Amount DECIMAL(18,2) NULL,
            BillableAmount DECIMAL(18,2) NULL
        );
        CREATE TABLE dbo.BillCredit (
            Id BIGINT NOT NULL PRIMARY KEY DEFAULT 1,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            VendorId BIGINT NULL,
            CreditNumber NVARCHAR(50) NULL,
            CreditDate DATETIME2(3) NULL
        );
        CREATE TABLE dbo.BillCreditLineItemAttachment (
            BillCreditLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        """
    )
    cur.execute(
        """
        ALTER TABLE dbo.BillLineItem ADD
            Amount DECIMAL(18,2) NULL,
            ProjectId BIGINT NULL,
            IsBillable BIT NOT NULL DEFAULT 1,
            IsBilled BIT NOT NULL DEFAULT 0,
            IsDraft BIT NOT NULL DEFAULT 0,
            Description NVARCHAR(255) NULL,
            Markup DECIMAL(18,2) NULL,
            Price DECIMAL(18,2) NULL;
        """
    )
    _ensure_prod_columns(cur)
    _exec_batches(cur, MIGRATION_SQL)
    _exec_batches_matching(
        cur,
        BILL_SQL,
        "CREATE OR ALTER PROCEDURE FinalizeBillById",
        "CREATE OR ALTER PROCEDURE TransitionBillStatus",
    )
    _exec_batches_matching(
        cur,
        EXPENSE_SQL,
        "CREATE OR ALTER PROCEDURE FinalizeExpenseById",
        "CREATE OR ALTER PROCEDURE TransitionExpenseStatus",
    )
    _exec_batches_matching(
        cur,
        EXPENSE_CODING_SQL,
        "CREATE OR ALTER PROCEDURE ReadVendorDominantSubCostCode",
    )
    _exec_batches_matching(
        cur,
        BILL_CODING_SQL,
        "CREATE OR ALTER FUNCTION dbo.BillDominantVoteForVendor",
        "CREATE OR ALTER PROCEDURE ReadVendorDominantSubCostCodeForBills",
    )


@pytest.fixture()
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    _drop_all(cur)
    _install_minimal_schema(cur)
    yield c
    _drop_all(cur)
    c.close()


def _insert_scc(cur, scc_id: int, number: str, *, holding: bool = False) -> None:
    cur.execute(
        """
        INSERT INTO dbo.SubCostCode (Id, Number, Name, CostCodeId, IsHoldingCode)
        VALUES (?, ?, ?, 1, ?)
        """,
        scc_id,
        number,
        f"Name-{number}",
        1 if holding else 0,
    )


def _bill_id(cur, *, status: str = "draft") -> int:
    cur.execute(
        "INSERT INTO dbo.Bill (Status) OUTPUT INSERTED.Id VALUES (?)",
        status,
    )
    return int(cur.fetchone()[0])


def _exec_expect_holding_refusal(cur, sql: str, *params) -> None:
    with pytest.raises(pyodbc.Error, match="HOLDING_CODE"):
        cur.execute(sql, params)
        if cur.description:
            cur.fetchone()


def _expense_id(cur, *, status: str = "draft") -> int:
    cur.execute(
        "INSERT INTO dbo.Expense (Status) OUTPUT INSERTED.Id VALUES (?)",
        status,
    )
    return int(cur.fetchone()[0])


def test_finalize_bill_refuses_holding_line_and_leaves_status(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "00.02", holding=True)
    _insert_scc(cur, 2, "01.01", holding=False)
    bid = _bill_id(cur)
    cur.execute(
        "INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId) VALUES (?, ?)",
        bid,
        1,
    )
    conn.commit()
    _exec_expect_holding_refusal(cur, "EXEC dbo.FinalizeBillById @Id=?", bid)
    cur.execute("SELECT Status FROM dbo.Bill WHERE Id=?", bid)
    assert cur.fetchone()[0] == "draft"


def test_finalize_bill_completes_clean_document(conn):
    cur = conn.cursor()
    _insert_scc(cur, 2, "01.01", holding=False)
    bid = _bill_id(cur)
    cur.execute(
        "INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId) VALUES (?, ?)",
        bid,
        2,
    )
    conn.commit()
    cur.execute("EXEC dbo.FinalizeBillById @Id=?", bid)
    row = cur.fetchone()
    assert row is not None
    cur.execute("SELECT Status FROM dbo.Bill WHERE Id=?", bid)
    assert cur.fetchone()[0] == "completed"


def test_transition_bill_status_holding_rules(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "00.02", holding=True)
    bid = _bill_id(cur, status="submitted")
    cur.execute(
        "INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId) VALUES (?, ?)",
        bid,
        1,
    )
    conn.commit()
    for target in ("approved", "completed"):
        _exec_expect_holding_refusal(
            cur,
            "EXEC dbo.TransitionBillStatus @Id=?, @FromStatuses=?, @ToStatus=?",
            bid,
            "submitted",
            target,
        )
        cur.execute("SELECT Status FROM dbo.Bill WHERE Id=?", bid)
        assert cur.fetchone()[0] == "submitted"
    cur.execute(
        "EXEC dbo.TransitionBillStatus @Id=?, @FromStatuses=?, @ToStatus=?",
        bid,
        "submitted",
        "declined",
    )
    assert cur.fetchone() is not None
    cur.execute("SELECT Status FROM dbo.Bill WHERE Id=?", bid)
    assert cur.fetchone()[0] == "declined"


def test_finalize_expense_refuses_holding(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "00.02", holding=True)
    eid = _expense_id(cur)
    cur.execute(
        "INSERT INTO dbo.ExpenseLineItem (ExpenseId, SubCostCodeId) VALUES (?, ?)",
        eid,
        1,
    )
    conn.commit()
    _exec_expect_holding_refusal(cur, "EXEC dbo.FinalizeExpenseById @Id=?", eid)
    cur.execute("SELECT Status FROM dbo.Expense WHERE Id=?", eid)
    assert cur.fetchone()[0] == "draft"


def test_read_vendor_dominant_sub_cost_code_ignores_holding_lines(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "00.02", holding=True)
    _insert_scc(cur, 2, "01.01", holding=False)
    for _ in range(3):
        eid = _expense_id(cur, status="completed")
        cur.execute("UPDATE dbo.Expense SET VendorId=5, IsDraft=0 WHERE Id=?", eid)
        cur.execute(
            "INSERT INTO dbo.ExpenseLineItem (ExpenseId, SubCostCodeId, IsDraft) VALUES (?, ?, 0)",
            eid,
            2,
        )
    for _ in range(5):
        eid = _expense_id(cur, status="completed")
        cur.execute("UPDATE dbo.Expense SET VendorId=5, IsDraft=0 WHERE Id=?", eid)
        cur.execute(
            "INSERT INTO dbo.ExpenseLineItem (ExpenseId, SubCostCodeId, IsDraft) VALUES (?, ?, 0)",
            eid,
            1,
        )
    conn.commit()
    cur.execute("EXEC dbo.ReadVendorDominantSubCostCode @VendorId=5")
    row = cur.fetchone()
    assert row is not None
    assert int(row[0]) == 2


def test_read_vendor_dominant_sub_cost_code_for_bills_ignores_holding(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "00.02", holding=True)
    _insert_scc(cur, 2, "01.01", holding=False)
    for _ in range(3):
        bid = _bill_id(cur, status="draft")
        cur.execute(
            "UPDATE dbo.Bill SET VendorId=7, IsDraft=0 WHERE Id=?",
            bid,
        )
        cur.execute(
            """
            INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId, Amount, ProjectId, IsDraft)
            VALUES (?, ?, 100, 10, 0)
            """,
            bid,
            2,
        )
    for _ in range(4):
        bid = _bill_id(cur, status="draft")
        cur.execute("UPDATE dbo.Bill SET VendorId=7, IsDraft=0 WHERE Id=?", bid)
        cur.execute(
            """
            INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId, Amount, ProjectId, IsDraft)
            VALUES (?, ?, 100, 10, 0)
            """,
            bid,
            1,
        )
    conn.commit()
    cur.execute("EXEC dbo.ReadVendorDominantSubCostCodeForBills @VendorId=7")
    row = cur.fetchone()
    assert row is not None
    assert int(row[0]) == 2


def test_transition_expense_status_holding_rules(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "00.02", holding=True)
    eid = _expense_id(cur, status="submitted")
    cur.execute(
        "INSERT INTO dbo.ExpenseLineItem (ExpenseId, SubCostCodeId) VALUES (?, ?)",
        eid,
        1,
    )
    conn.commit()
    for target in ("approved", "completed"):
        _exec_expect_holding_refusal(
            cur,
            "EXEC dbo.TransitionExpenseStatus @Id=?, @FromStatuses=?, @ToStatus=?",
            eid,
            "submitted",
            target,
        )
        cur.execute("SELECT Status FROM dbo.Expense WHERE Id=?", eid)
        assert cur.fetchone()[0] == "submitted"


def test_billable_ready_sql_excludes_holding_line(conn):
    from entities.invoice.business.service import (
        BILLABLE_READY_BILL_CREDIT_LINE_ITEMS_SQL,
        BILLABLE_READY_BILL_LINE_ITEMS_SQL,
        BILLABLE_READY_EXPENSE_LINE_ITEMS_SQL,
    )

    cur = conn.cursor()
    _insert_scc(cur, 1, "00.02", holding=True)
    _insert_scc(cur, 2, "01.01", holding=False)
    bid = _bill_id(cur, status="draft")
    cur.execute(
        "UPDATE dbo.Bill SET Status=N'completed', VendorId=1 WHERE Id=?",
        bid,
    )
    cur.execute(
        """
        INSERT INTO dbo.BillLineItem
            (BillId, SubCostCodeId, ProjectId, IsBillable, IsBilled, IsDraft, Amount)
        VALUES (?, ?, 99, 1, 0, 0, 10)
        """,
        bid,
        1,
    )
    cur.execute(
        """
        INSERT INTO dbo.BillLineItem
            (BillId, SubCostCodeId, ProjectId, IsBillable, IsBilled, IsDraft, Amount)
        VALUES (?, ?, 99, 1, 0, 0, 20)
        """,
        bid,
        2,
    )
    cur.execute(
        """
        INSERT INTO dbo.BillLineItem
            (BillId, SubCostCodeId, ProjectId, IsBillable, IsBilled, IsDraft, Amount)
        VALUES (?, ?, 99, 1, 0, 0, 5)
        """,
        bid,
        None,
    )
    conn.commit()
    cur.execute(BILLABLE_READY_BILL_LINE_ITEMS_SQL, 99)
    bill_rows = cur.fetchall()
    assert len(bill_rows) == 2
    cur.execute(
        "SELECT Id FROM dbo.BillLineItem WHERE BillId=? AND SubCostCodeId=1",
        bid,
    )
    holding_line_ids = {int(r[0]) for r in cur.fetchall()}
    assert not holding_line_ids.intersection({int(r[0]) for r in bill_rows})
    cur.execute(BILLABLE_READY_EXPENSE_LINE_ITEMS_SQL, 99)
    assert len(cur.fetchall()) == 0
    cur.execute(BILLABLE_READY_BILL_CREDIT_LINE_ITEMS_SQL, 99)
    assert len(cur.fetchall()) == 0


def test_subcostcode_column_from_base_and_migration_on_legacy_table(conn):
    cur = conn.cursor()
    cur.execute("IF OBJECT_ID('dbo.SubCostCodeLegacy', 'U') IS NOT NULL DROP TABLE dbo.SubCostCodeLegacy")
    cur.execute(
        """
        CREATE TABLE dbo.SubCostCodeLegacy (
            Id BIGINT NOT NULL PRIMARY KEY,
            Number NVARCHAR(50) NOT NULL,
            Name NVARCHAR(255) NOT NULL,
            CostCodeId BIGINT NOT NULL DEFAULT 1
        );
        INSERT INTO dbo.SubCostCodeLegacy (Id, Number, Name) VALUES (50, N'00.02', N'Legacy');
        """
    )
    conn.commit()
    sub_base = SUBCOSTCODE_SQL.read_text(encoding="utf-8")
    if "IF COL_LENGTH('dbo.SubCostCode', 'IsHoldingCode')" in sub_base:
        pass
    _exec_batches(cur, MIGRATION_SQL)
    cur.execute(
        "IF COL_LENGTH('dbo.SubCostCodeLegacy', 'IsHoldingCode') IS NULL "
        "ALTER TABLE dbo.SubCostCodeLegacy ADD IsHoldingCode BIT NOT NULL DEFAULT 0"
    )
    cur.execute("UPDATE dbo.SubCostCodeLegacy SET IsHoldingCode=1 WHERE Number=N'00.02'")
    cur.execute("SELECT IsHoldingCode FROM dbo.SubCostCodeLegacy WHERE Number=N'00.02'")
    assert cur.fetchone()[0] is True


_LINE_ARGS = (
    "@ProjectId=NULL, @Description=N'd', @Quantity=1, @Rate=1, @Amount=1, "
    "@IsBillable=1, @IsBilled=0, @Markup=0, @Price=1"
)


def _create_line(cur, kind: str, parent_id: int, scc_id: int):
    parent = "BillId" if kind == "Bill" else "ExpenseId"
    cur.execute(
        f"EXEC dbo.Create{kind}LineItem @{parent}=?, @SubCostCodeId=?, {_LINE_ARGS}",
        parent_id,
        scc_id,
    )
    row = cur.fetchone()
    while cur.nextset():
        pass
    return row


def _update_line_sql(kind: str) -> str:
    parent = "BillId" if kind == "Bill" else "ExpenseId"
    return (
        f"EXEC dbo.Update{kind}LineItemById @Id=?, @RowVersion=?, @{parent}=?, "
        f"@SubCostCodeId=?, {_LINE_ARGS}"
    )


def _row_version(cur, kind: str, line_id: int) -> bytes:
    cur.execute(f"SELECT RowVersion FROM dbo.{kind}LineItem WHERE Id = ?", line_id)
    return bytes(cur.fetchone()[0])


def _scc_of(cur, kind: str, line_id: int):
    cur.execute(f"SELECT SubCostCodeId FROM dbo.{kind}LineItem WHERE Id = ?", line_id)
    return cur.fetchone()[0]


@pytest.mark.parametrize("kind", ["Bill", "Expense"])
def test_f3_approved_parent_line_sprocs_refuse_holding_scc(conn, kind):
    cur = conn.cursor()
    _insert_scc(cur, 1, "00.02", holding=True)
    _insert_scc(cur, 2, "01.01", holding=False)
    _insert_scc(cur, 3, "01.02", holding=False)
    sql_path = BILL_LINE_ITEM_SQL if kind == "Bill" else EXPENSE_LINE_ITEM_SQL
    _exec_batches_matching(
        cur,
        sql_path,
        f"CREATE OR ALTER PROCEDURE Create{kind}LineItem",
        f"CREATE OR ALTER PROCEDURE Update{kind}LineItemById",
    )
    # Draft parent: the holding code is allowed (the rule only bars approved+).
    draft_id = _bill_id(cur, status="draft") if kind == "Bill" else _expense_id(cur, status="draft")
    conn.commit()
    assert _create_line(cur, kind, draft_id, 1) is not None
    conn.commit()

    # Approved parent: a real code is accepted on create...
    parent_id = _bill_id(cur, status="approved") if kind == "Bill" else _expense_id(cur, status="approved")
    conn.commit()
    created = _create_line(cur, kind, parent_id, 2)
    conn.commit()
    assert created is not None
    cur.execute(f"SELECT MAX(Id) FROM dbo.{kind}LineItem WHERE {kind}Id = ?", parent_id)
    line_id = int(cur.fetchone()[0])

    # ...but a holding code is refused on create, and nothing is written.
    cur.execute(f"SELECT COUNT(*) FROM dbo.{kind}LineItem WHERE {kind}Id = ?", parent_id)
    before = cur.fetchone()[0]
    with pytest.raises(pyodbc.Error, match="HOLDING_CODE"):
        _create_line(cur, kind, parent_id, 1)
    conn.rollback()
    cur.execute(f"SELECT COUNT(*) FROM dbo.{kind}LineItem WHERE {kind}Id = ?", parent_id)
    assert cur.fetchone()[0] == before

    # Update to a holding code is refused and the line keeps its code.
    with pytest.raises(pyodbc.Error, match="HOLDING_CODE"):
        cur.execute(_update_line_sql(kind), line_id, _row_version(cur, kind, line_id), parent_id, 1)
        cur.fetchall()
    conn.rollback()
    assert _scc_of(cur, kind, line_id) == 2

    # Update to another real code still works.
    cur.execute(_update_line_sql(kind), line_id, _row_version(cur, kind, line_id), parent_id, 3)
    cur.fetchall()
    while cur.nextset():
        pass
    conn.commit()
    assert _scc_of(cur, kind, line_id) == 3


def test_g9_create_review_refuses_approval_with_holding_line(conn):
    cur = conn.cursor()
    cur.execute(
        """
        IF OBJECT_ID('dbo.vw_Review', 'V') IS NOT NULL DROP VIEW dbo.vw_Review;
        IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL DROP TABLE dbo.Review;
        IF OBJECT_ID('dbo.ReviewStatus', 'U') IS NOT NULL DROP TABLE dbo.ReviewStatus;
        CREATE TABLE dbo.ReviewStatus (
            Id BIGINT NOT NULL PRIMARY KEY,
            Name NVARCHAR(50) NOT NULL,
            SortOrder INT NOT NULL,
            IsFinal BIT NOT NULL,
            IsDeclined BIT NOT NULL,
            IsInitial BIT NOT NULL DEFAULT 0,
            Color NVARCHAR(20) NULL
        );
        INSERT INTO dbo.ReviewStatus (Id, Name, SortOrder, IsFinal, IsDeclined, IsInitial)
        VALUES (1, N'approved', 3, 1, 0, 0);
        CREATE TABLE dbo.Review (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            RowVersion ROWVERSION NOT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            ReviewStatusId BIGINT NOT NULL,
            UserId BIGINT NOT NULL,
            Comments NVARCHAR(MAX) NULL,
            BillId BIGINT NULL,
            ExpenseId BIGINT NULL,
            BillCreditId BIGINT NULL,
            InvoiceId BIGINT NULL,
            ContractLaborId BIGINT NULL,
            EmailMessageId BIGINT NULL,
            CreatedByUserId BIGINT NOT NULL DEFAULT 17,
            ReviewKind NVARCHAR(20) NULL,
            IdempotencyKey UNIQUEIDENTIFIER NULL
        );
        """
    )
    cur.execute(
        """
        CREATE VIEW dbo.vw_Review AS
        SELECT
            r.Id, r.PublicId, r.RowVersion, r.CreatedDatetime, r.ModifiedDatetime,
            r.ReviewStatusId, r.UserId, r.Comments,
            r.BillId, r.ExpenseId, r.BillCreditId, r.InvoiceId, r.ContractLaborId,
            r.EmailMessageId, r.CreatedByUserId, r.ReviewKind, r.IdempotencyKey,
            rs.Name AS StatusName, rs.SortOrder AS StatusSortOrder,
            rs.IsFinal AS StatusIsFinal, rs.IsDeclined AS StatusIsDeclined,
            rs.IsInitial AS StatusIsInitial, rs.Color AS StatusColor,
            CAST(NULL AS NVARCHAR(50)) AS UserFirstname,
            CAST(NULL AS NVARCHAR(50)) AS UserLastname
        FROM dbo.Review r
        LEFT JOIN dbo.ReviewStatus rs ON rs.Id = r.ReviewStatusId;
        """
    )
    _exec_batches_matching(cur, REVIEW_SQL, "CREATE OR ALTER PROCEDURE CreateReview")
    _insert_scc(cur, 1, "00.02", holding=True)
    bid = _bill_id(cur, status="submitted")
    cur.execute(
        "INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId) VALUES (?, ?)",
        bid,
        1,
    )
    conn.commit()
    _exec_expect_holding_refusal(
        cur,
        "EXEC dbo.CreateReview @ReviewStatusId=1, @UserId=17, @BillId=?",
        bid,
    )
    cur.execute("SELECT COUNT(*) FROM dbo.Review WHERE BillId=?", bid)
    assert int(cur.fetchone()[0]) == 0


def test_read_holding_sub_cost_code_ids_sproc(conn):
    cur = conn.cursor()
    _exec_batches_matching(
        cur,
        SUBCOSTCODE_SQL,
        "CREATE OR ALTER PROCEDURE ReadHoldingSubCostCodeIds",
    )
    _insert_scc(cur, 501, "00.02", holding=True)
    _insert_scc(cur, 502, "01.01", holding=False)
    conn.commit()
    cur.execute("EXEC dbo.ReadHoldingSubCostCodeIds")
    ids = {int(row.Id) for row in cur.fetchall()}
    assert ids == {501}


def test_migration_flags_00_02_and_is_rerunnable(conn):
    cur = conn.cursor()
    _insert_scc(cur, 10, "00.02", holding=False)
    _insert_scc(cur, 11, "01.01", holding=False)
    conn.commit()
    _exec_batches(cur, MIGRATION_SQL)
    _exec_batches(cur, MIGRATION_SQL)
    cur.execute("SELECT IsHoldingCode FROM dbo.SubCostCode WHERE Number=N'00.02'")
    assert cur.fetchone()[0] is True
    cur.execute("SELECT IsHoldingCode FROM dbo.SubCostCode WHERE Number=N'01.01'")
    assert cur.fetchone()[0] is False
