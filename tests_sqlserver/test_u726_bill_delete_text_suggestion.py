"""U-726: DeleteBillCascadeById must clear BillTextCodingSuggestion (FK NO ACTION)."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pyodbc
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}

REPO_ROOT = Path(__file__).resolve().parents[1]
BILL_SQL = REPO_ROOT / "entities/bill/sql/dbo.bill.sql"


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
    raise RuntimeError("tests_sqlserver refuses non-local SERVER")


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


def _exec_sproc_batch(cur, path: Path, proc_name: str) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        if f"PROCEDURE {proc_name}" in batch or f"PROCEDURE dbo.{proc_name}" in batch:
            cur.execute(batch)
            return
    raise RuntimeError(f"{proc_name} batch missing from {path}")


def _drain(cur):
    while True:
        if cur.description:
            cur.fetchall()
        if not cur.nextset():
            break


def _call_delete_bill_cascade(cur, bill_id: int):
    cur.execute("SET NOCOUNT ON;")
    _drain(cur)
    cur.execute("EXEC dbo.DeleteBillCascadeById @Id = ?", bill_id)
    assert cur.description is not None, "DeleteBillCascadeById must return OUTPUT row"
    row = cur.fetchone()
    _drain(cur)
    return row


def _drop_all(cur) -> None:
    cur.execute(
        "IF OBJECT_ID('dbo.DeleteBillCascadeById', 'P') IS NOT NULL "
        "DROP PROCEDURE dbo.DeleteBillCascadeById"
    )
    for tbl in (
        "dbo.BillTextCodingSuggestion",
        "dbo.InvoiceLineItemAttachment",
        "dbo.InvoiceLineItemSourceProvenance",
        "dbo.InvoiceLineItem",
        "dbo.BillLineItemAttachment",
        "dbo.BillLineItem",
        "dbo.ContractLabor",
        "dbo.ReviewEntry",
        "dbo.Review",
        "dbo.Attachment",
        "dbo.Bill",
        "dbo.Vendor",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Vendor (Id BIGINT NOT NULL PRIMARY KEY, Name NVARCHAR(255) NOT NULL);
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
            StatusOrigin NVARCHAR(50) NULL,
            StatusSourceRef NVARCHAR(100) NULL,
            IntakeSource NVARCHAR(20) NULL,
            IntakeSourceDetail NVARCHAR(100) NULL,
            SourceEmailMessageId BIGINT NULL
        );
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillId BIGINT NOT NULL
        );
        CREATE TABLE dbo.BillLineItemAttachment (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        CREATE TABLE dbo.InvoiceLineItem (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillLineItemId BIGINT NULL
        );
        CREATE TABLE dbo.InvoiceLineItemAttachment (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            InvoiceLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        CREATE TABLE dbo.InvoiceLineItemSourceProvenance (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            InvoiceLineItemId BIGINT NOT NULL
        );
        CREATE TABLE dbo.ContractLabor (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillLineItemId BIGINT NULL,
            ModifiedDatetime DATETIME2(3) NULL
        );
        CREATE TABLE dbo.Review (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillId BIGINT NOT NULL
        );
        CREATE TABLE dbo.ReviewEntry (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillId BIGINT NOT NULL
        );
        CREATE TABLE dbo.Attachment (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID()
        );
        CREATE TABLE dbo.BillTextCodingSuggestion (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            CreatedDatetime DATETIME2(3) NOT NULL,
            ModifiedDatetime DATETIME2(3) NOT NULL,
            BillId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL,
            ExtractedDatetime DATETIME2(3) NOT NULL,
            Status NVARCHAR(20) NOT NULL,
            SubCostCodeId BIGINT NULL,
            ProjectId BIGINT NULL,
            Confidence DECIMAL(5,4) NULL,
            Mode CHAR(1) NULL,
            WinningModel NVARCHAR(100) NULL,
            ModelVersion NVARCHAR(50) NOT NULL,
            FailureReason NVARCHAR(50) NULL,
            CallDatetime DATETIME2(3) NULL,
            CONSTRAINT FK_BillTextCodingSuggestion_Bill
                FOREIGN KEY (BillId) REFERENCES dbo.Bill(Id),
            CONSTRAINT FK_BillTextCodingSuggestion_Attachment
                FOREIGN KEY (AttachmentId) REFERENCES dbo.Attachment(Id),
            CONSTRAINT CK_BillTextCodingSuggestion_Status
                CHECK (Status IN ('pending','done','failed','skipped'))
        );
        """
    )
    _exec_sproc_batch(cur, BILL_SQL, "DeleteBillCascadeById")


@pytest.fixture()
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    db_name = cur.execute("SELECT DB_NAME()").fetchval()
    assert db_name == "buildone_test", f"refusing to run against database {db_name!r}"
    _drop_all(cur)
    _install_schema(cur)
    yield c
    _drop_all(cur)
    c.close()


def _insert_draft_bill(cur, *, vendor_id: int = 1) -> tuple[int, uuid.UUID]:
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (?, 'V');", vendor_id)
    cur.execute(
        """
        INSERT INTO dbo.Bill (VendorId, Status, BillNumber)
        OUTPUT INSERTED.Id, INSERTED.PublicId
        VALUES (?, 'draft', 'U726-TEST')
        """,
        vendor_id,
    )
    row = cur.fetchone()
    return int(row[0]), row[1]


def _insert_text_suggestion(cur, *, bill_id: int) -> int:
    cur.execute("INSERT INTO dbo.Attachment DEFAULT VALUES;")
    attachment_id = int(cur.execute("SELECT MAX(Id) FROM dbo.Attachment").fetchval())
    cur.execute(
        """
        INSERT INTO dbo.BillTextCodingSuggestion (
            CreatedDatetime, ModifiedDatetime, BillId, AttachmentId,
            ExtractedDatetime, Status, ModelVersion
        )
        OUTPUT INSERTED.Id
        VALUES (
            SYSUTCDATETIME(), SYSUTCDATETIME(), ?, ?,
            SYSUTCDATETIME(), 'pending', 'test-v1'
        )
        """,
        bill_id,
        attachment_id,
    )
    return int(cur.fetchone()[0])


def test_draft_with_text_suggestion_deletes_cleanly(conn):
    cur = conn.cursor()
    bill_id, public_id = _insert_draft_bill(cur)
    suggestion_id = _insert_text_suggestion(cur, bill_id=bill_id)

    row = _call_delete_bill_cascade(cur, bill_id)

    assert row is not None
    assert int(row[0]) == bill_id
    assert row[1] == public_id
    cur.execute("SELECT COUNT(*) FROM dbo.Bill WHERE Id = ?", bill_id)
    assert cur.fetchval() == 0
    cur.execute(
        "SELECT COUNT(*) FROM dbo.BillTextCodingSuggestion WHERE Id = ?", suggestion_id
    )
    assert cur.fetchval() == 0


def test_draft_without_suggestion_still_deletes(conn):
    cur = conn.cursor()
    bill_id, _ = _insert_draft_bill(cur)
    row = _call_delete_bill_cascade(cur, bill_id)
    assert row is not None
    assert int(row[0]) == bill_id


def test_other_bills_suggestion_untouched(conn):
    cur = conn.cursor()
    keep_id, _ = _insert_draft_bill(cur, vendor_id=1)
    delete_id, _ = _insert_draft_bill(cur, vendor_id=2)
    keep_suggestion = _insert_text_suggestion(cur, bill_id=keep_id)
    _insert_text_suggestion(cur, bill_id=delete_id)

    _call_delete_bill_cascade(cur, delete_id)

    cur.execute("SELECT COUNT(*) FROM dbo.Bill WHERE Id = ?", keep_id)
    assert cur.fetchval() == 1
    cur.execute(
        "SELECT COUNT(*) FROM dbo.BillTextCodingSuggestion WHERE Id = ?", keep_suggestion
    )
    assert cur.fetchval() == 1
