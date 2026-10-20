"""U-684a: DeleteBillCreditCascadeById / DeleteInvoiceCascadeById on local SQL Server."""
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

REPO_ROOT = Path(__file__).resolve().parents[1]
BC_SQL = REPO_ROOT / "entities/bill_credit/sql/dbo.bill_credit.sql"
INV_SQL = REPO_ROOT / "entities/invoice/sql/dbo.invoice.sql"


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


def _call_cascade(cur, proc: str, entity_id: int):
    cur.execute("SET NOCOUNT ON;")
    _drain(cur)
    cur.execute(f"EXEC dbo.{proc} @Id = ?", entity_id)
    assert cur.description is not None, f"{proc} must return a result set (even when empty)"
    row = cur.fetchone()
    _drain(cur)
    return row


def _expect_raiserror(cur, proc: str, entity_id: int):
    with pytest.raises(pyodbc.Error) as exc_info:
        _call_cascade(cur, proc, entity_id)
    return str(exc_info.value)


def _drop_all(cur) -> None:
    for proc in ("DeleteBillCreditCascadeById", "DeleteInvoiceCascadeById"):
        cur.execute(f"IF OBJECT_ID('dbo.{proc}', 'P') IS NOT NULL DROP PROCEDURE dbo.{proc}")
    for tbl in (
        "dbo._U684aBillCreditBlocker",
        "dbo._U684aInvoiceBlocker",
        "dbo.InvoiceLineItemAttachment",
        "dbo.InvoiceLineItemSourceProvenance",
        "dbo.InvoiceLineItem",
        "dbo.InvoiceAttachment",
        "dbo.BillCreditLineItemAttachment",
        "dbo.BillCreditLineItem",
        "dbo.Review",
        "dbo.Attachment",
        "dbo.Invoice",
        "dbo.BillCredit",
        "dbo.Vendor",
        "dbo.Project",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Vendor (Id BIGINT NOT NULL PRIMARY KEY, Name NVARCHAR(255) NOT NULL);
        CREATE TABLE dbo.Project (Id BIGINT NOT NULL PRIMARY KEY, Name NVARCHAR(255) NULL);
        CREATE TABLE dbo.Attachment (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            BlobUrl NVARCHAR(500) NULL
        );
        CREATE TABLE dbo.BillCredit (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            RowVersion ROWVERSION NOT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            VendorId BIGINT NOT NULL,
            CreditDate DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            CreditNumber NVARCHAR(50) NOT NULL,
            TotalAmount DECIMAL(18,2) NULL,
            Memo NVARCHAR(MAX) NULL,
            IsDraft BIT NOT NULL DEFAULT 1,
            QboId NVARCHAR(50) NULL,
            RealmId NVARCHAR(50) NULL
        );
        CREATE TABLE dbo.BillCreditLineItem (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            BillCreditId BIGINT NOT NULL,
            ProjectId BIGINT NULL,
            Amount DECIMAL(18,2) NULL,
            CONSTRAINT FK_BillCreditLineItem_BillCredit
                FOREIGN KEY (BillCreditId) REFERENCES dbo.BillCredit(Id) ON DELETE CASCADE
        );
        CREATE TABLE dbo.BillCreditLineItemAttachment (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillCreditLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL,
            CONSTRAINT FK_BillCreditLineItemAttachment_BillCreditLineItem
                FOREIGN KEY (BillCreditLineItemId) REFERENCES dbo.BillCreditLineItem(Id),
            CONSTRAINT FK_BillCreditLineItemAttachment_Attachment
                FOREIGN KEY (AttachmentId) REFERENCES dbo.Attachment(Id)
        );
        CREATE TABLE dbo.Invoice (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            RowVersion ROWVERSION NOT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            ProjectId BIGINT NOT NULL,
            PaymentTermId BIGINT NULL,
            InvoiceDate DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            DueDate DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            InvoiceNumber NVARCHAR(50) NOT NULL,
            TotalAmount DECIMAL(18,2) NULL,
            Memo NVARCHAR(MAX) NULL,
            IsDraft BIT NOT NULL DEFAULT 1,
            QboId NVARCHAR(50) NULL,
            RealmId NVARCHAR(50) NULL
        );
        CREATE TABLE dbo.InvoiceLineItem (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            InvoiceId BIGINT NOT NULL,
            BillCreditLineItemId BIGINT NULL,
            SourceType NVARCHAR(50) NULL,
            Amount DECIMAL(18,2) NULL,
            CONSTRAINT FK_InvoiceLineItem_Invoice
                FOREIGN KEY (InvoiceId) REFERENCES dbo.Invoice(Id),
            CONSTRAINT FK_InvoiceLineItem_BillCreditLineItem
                FOREIGN KEY (BillCreditLineItemId) REFERENCES dbo.BillCreditLineItem(Id)
        );
        CREATE TABLE dbo.InvoiceLineItemAttachment (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            InvoiceLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL,
            CONSTRAINT FK_InvoiceLineItemAttachment_InvoiceLineItem
                FOREIGN KEY (InvoiceLineItemId) REFERENCES dbo.InvoiceLineItem(Id),
            CONSTRAINT FK_InvoiceLineItemAttachment_Attachment
                FOREIGN KEY (AttachmentId) REFERENCES dbo.Attachment(Id)
        );
        CREATE TABLE dbo.InvoiceLineItemSourceProvenance (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            InvoiceLineItemId BIGINT NOT NULL,
            CONSTRAINT FK_InvoiceLineItemSourceProvenance_InvoiceLineItem
                FOREIGN KEY (InvoiceLineItemId) REFERENCES dbo.InvoiceLineItem(Id)
        );
        CREATE TABLE dbo.InvoiceAttachment (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            InvoiceId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL,
            CONSTRAINT FK_InvoiceAttachment_Invoice
                FOREIGN KEY (InvoiceId) REFERENCES dbo.Invoice(Id),
            CONSTRAINT FK_InvoiceAttachment_Attachment
                FOREIGN KEY (AttachmentId) REFERENCES dbo.Attachment(Id)
        );
        CREATE TABLE dbo.Review (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            BillCreditId BIGINT NULL,
            InvoiceId BIGINT NULL,
            CONSTRAINT FK_Review_BillCredit
                FOREIGN KEY (BillCreditId) REFERENCES dbo.BillCredit(Id),
            CONSTRAINT FK_Review_Invoice
                FOREIGN KEY (InvoiceId) REFERENCES dbo.Invoice(Id)
        );
        CREATE TABLE dbo._U684aBillCreditBlocker (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            BillCreditId BIGINT NOT NULL,
            CONSTRAINT FK_U684aBlocker_BillCredit
                FOREIGN KEY (BillCreditId) REFERENCES dbo.BillCredit(Id)
        );
        CREATE TABLE dbo._U684aInvoiceBlocker (
            Id BIGINT IDENTITY(1,1) PRIMARY KEY,
            InvoiceId BIGINT NOT NULL,
            CONSTRAINT FK_U684aBlocker_Invoice
                FOREIGN KEY (InvoiceId) REFERENCES dbo.Invoice(Id)
        );
        """
    )
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (1, 'V');")
    cur.execute("INSERT INTO dbo.Project (Id, Name) VALUES (1, 'P');")


@pytest.fixture
def conn():
    cs = os.environ["BUILDONE_TEST_SQLSERVER"]
    connection = _sqlserver_connect(cs)
    try:
        cur = connection.cursor()
        cur.execute("SET LOCK_TIMEOUT 10000")
        _drop_all(cur)
        _install_schema(cur)
        _exec_sproc_batch(cur, BC_SQL, "DeleteBillCreditCascadeById")
        _exec_sproc_batch(cur, INV_SQL, "DeleteInvoiceCascadeById")
        connection.commit()
        yield connection
    finally:
        try:
            cur = connection.cursor()
            cur.execute("SET LOCK_TIMEOUT 10000")
            _drop_all(cur)
            connection.commit()
        finally:
            connection.close()


def _count(cur, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return int(cur.fetchone()[0])


def _bill_credit_child_counts(cur) -> dict[str, int]:
    return {
        "dbo.BillCredit": _count(cur, "dbo.BillCredit"),
        "dbo.BillCreditLineItem": _count(cur, "dbo.BillCreditLineItem"),
        "dbo.BillCreditLineItemAttachment": _count(cur, "dbo.BillCreditLineItemAttachment"),
        "dbo.Review": _count(cur, "dbo.Review"),
        "dbo.Attachment": _count(cur, "dbo.Attachment"),
    }


def _invoice_child_counts(cur) -> dict[str, int]:
    return {
        "dbo.Invoice": _count(cur, "dbo.Invoice"),
        "dbo.InvoiceLineItem": _count(cur, "dbo.InvoiceLineItem"),
        "dbo.InvoiceLineItemAttachment": _count(cur, "dbo.InvoiceLineItemAttachment"),
        "dbo.InvoiceLineItemSourceProvenance": _count(cur, "dbo.InvoiceLineItemSourceProvenance"),
        "dbo.InvoiceAttachment": _count(cur, "dbo.InvoiceAttachment"),
        "dbo.Review": _count(cur, "dbo.Review"),
        "dbo.Attachment": _count(cur, "dbo.Attachment"),
    }


def _assert_counts_unchanged(cur, before: dict[str, int]) -> None:
    for table, n in before.items():
        assert _count(cur, table) == n, table


def _seed_bill_credit_with_children(cur, *, is_draft: int, qbo_id=None, credit_number: str = "CR"):
    cur.execute(
        """
        INSERT INTO dbo.BillCredit (VendorId, CreditNumber, IsDraft, QboId)
        OUTPUT INSERTED.Id VALUES (1, ?, ?, ?);
        """,
        credit_number,
        is_draft,
        qbo_id,
    )
    bc_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.BillCreditLineItem (BillCreditId, ProjectId) OUTPUT INSERTED.Id VALUES (?, 1);",
        bc_id,
    )
    bcli_id = int(cur.fetchone()[0])
    cur.execute("INSERT INTO dbo.Attachment (BlobUrl) OUTPUT INSERTED.Id VALUES (?);", credit_number)
    att_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.BillCreditLineItemAttachment (BillCreditLineItemId, AttachmentId) VALUES (?, ?);",
        bcli_id,
        att_id,
    )
    cur.execute("INSERT INTO dbo.Review (BillCreditId) VALUES (?);", bc_id)
    return bc_id


def _seed_invoice_with_children(cur, *, is_draft: int, qbo_id=None, invoice_number: str = "INV"):
    cur.execute(
        """
        INSERT INTO dbo.Invoice (ProjectId, InvoiceNumber, IsDraft, QboId)
        OUTPUT INSERTED.Id VALUES (1, ?, ?, ?);
        """,
        invoice_number,
        is_draft,
        qbo_id,
    )
    inv_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.InvoiceLineItem (InvoiceId, SourceType) OUTPUT INSERTED.Id VALUES (?, 'Manual');",
        inv_id,
    )
    ili_id = int(cur.fetchone()[0])
    cur.execute("INSERT INTO dbo.Attachment (BlobUrl) OUTPUT INSERTED.Id VALUES (?);", invoice_number)
    att_line = int(cur.fetchone()[0])
    cur.execute("INSERT INTO dbo.Attachment (BlobUrl) OUTPUT INSERTED.Id VALUES (?);", invoice_number + "-h")
    att_hdr = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.InvoiceLineItemAttachment (InvoiceLineItemId, AttachmentId) VALUES (?, ?);",
        ili_id,
        att_line,
    )
    cur.execute(
        "INSERT INTO dbo.InvoiceLineItemSourceProvenance (InvoiceLineItemId) VALUES (?);",
        ili_id,
    )
    cur.execute(
        "INSERT INTO dbo.InvoiceAttachment (InvoiceId, AttachmentId) VALUES (?, ?);",
        inv_id,
        att_hdr,
    )
    cur.execute("INSERT INTO dbo.Review (InvoiceId) VALUES (?);", inv_id)
    return inv_id


# --- BillCredit ---


def test_bill_credit_cascade_removes_children_keeps_attachment(conn):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO dbo.BillCredit (VendorId, CreditNumber, IsDraft)
        OUTPUT INSERTED.Id VALUES (1, 'CR-1', 1);
        """
    )
    bc_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.BillCreditLineItem (BillCreditId, ProjectId) OUTPUT INSERTED.Id VALUES (?, 1);",
        bc_id,
    )
    bcli_id = int(cur.fetchone()[0])
    cur.execute("INSERT INTO dbo.Attachment (BlobUrl) OUTPUT INSERTED.Id VALUES ('x');")
    att_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.BillCreditLineItemAttachment (BillCreditLineItemId, AttachmentId) VALUES (?, ?);",
        bcli_id,
        att_id,
    )
    cur.execute(
        "INSERT INTO dbo.Review (BillCreditId) VALUES (?);",
        bc_id,
    )
    conn.commit()

    row = _call_cascade(cur, "DeleteBillCreditCascadeById", bc_id)
    conn.commit()
    assert row is not None
    assert _count(cur, "dbo.BillCredit") == 0
    assert _count(cur, "dbo.BillCreditLineItem") == 0
    assert _count(cur, "dbo.BillCreditLineItemAttachment") == 0
    assert _count(cur, "dbo.Review") == 0
    assert _count(cur, "dbo.Attachment") == 1


def test_bill_credit_non_draft_refused(conn):
    cur = conn.cursor()
    bc_id = _seed_bill_credit_with_children(cur, is_draft=0, credit_number="CR-ND")
    before = _bill_credit_child_counts(cur)
    conn.commit()
    err = _expect_raiserror(cur, "DeleteBillCreditCascadeById", bc_id)
    assert "STATUS_LOCKED" in err
    _assert_counts_unchanged(cur, before)


def test_bill_credit_qbo_refused(conn):
    cur = conn.cursor()
    bc_id = _seed_bill_credit_with_children(cur, is_draft=1, qbo_id="99", credit_number="CR-Q")
    before = _bill_credit_child_counts(cur)
    conn.commit()
    err = _expect_raiserror(cur, "DeleteBillCreditCascadeById", bc_id)
    assert "QuickBooks" in err
    _assert_counts_unchanged(cur, before)


def test_bill_credit_cited_by_invoice_refused(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.BillCredit (VendorId, CreditNumber, IsDraft) OUTPUT INSERTED.Id VALUES (1, 'CR-C', 1);"
    )
    bc_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.BillCreditLineItem (BillCreditId) OUTPUT INSERTED.Id VALUES (?);",
        bc_id,
    )
    bcli_id = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO dbo.Invoice (ProjectId, InvoiceNumber, IsDraft)
        OUTPUT INSERTED.Id VALUES (1, 'INV-C', 1);
        """
    )
    inv_id = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO dbo.InvoiceLineItem (InvoiceId, BillCreditLineItemId, SourceType)
        VALUES (?, ?, 'BillCreditLineItem');
        """,
        inv_id,
        bcli_id,
    )
    cur.execute("INSERT INTO dbo.Attachment (BlobUrl) OUTPUT INSERTED.Id VALUES ('cite');")
    att_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.BillCreditLineItemAttachment (BillCreditLineItemId, AttachmentId) VALUES (?, ?);",
        bcli_id,
        att_id,
    )
    cur.execute("INSERT INTO dbo.Review (BillCreditId) VALUES (?);", bc_id)
    before = _bill_credit_child_counts(cur)
    conn.commit()
    err = _expect_raiserror(cur, "DeleteBillCreditCascadeById", bc_id)
    assert "still cites" in err
    _assert_counts_unchanged(cur, before)


def test_bill_credit_missing_id_empty_result(conn):
    cur = conn.cursor()
    row = _call_cascade(cur, "DeleteBillCreditCascadeById", 99999)
    assert row is None


# --- Invoice ---


def test_invoice_cascade_removes_children_keeps_attachment(conn):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO dbo.Invoice (ProjectId, InvoiceNumber, IsDraft)
        OUTPUT INSERTED.Id VALUES (1, 'INV-1', 1);
        """
    )
    inv_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.InvoiceLineItem (InvoiceId, SourceType) OUTPUT INSERTED.Id VALUES (?, 'Manual');",
        inv_id,
    )
    ili_id = int(cur.fetchone()[0])
    cur.execute("INSERT INTO dbo.Attachment (BlobUrl) OUTPUT INSERTED.Id VALUES ('y');")
    att_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO dbo.InvoiceLineItemAttachment (InvoiceLineItemId, AttachmentId) VALUES (?, ?);",
        ili_id,
        att_id,
    )
    cur.execute(
        "INSERT INTO dbo.InvoiceLineItemSourceProvenance (InvoiceLineItemId) VALUES (?);",
        ili_id,
    )
    cur.execute(
        "INSERT INTO dbo.InvoiceAttachment (InvoiceId, AttachmentId) VALUES (?, ?);",
        inv_id,
        att_id,
    )
    cur.execute("INSERT INTO dbo.Review (InvoiceId) VALUES (?);", inv_id)
    conn.commit()

    row = _call_cascade(cur, "DeleteInvoiceCascadeById", inv_id)
    conn.commit()
    assert row is not None
    assert _count(cur, "dbo.Invoice") == 0
    assert _count(cur, "dbo.InvoiceLineItem") == 0
    assert _count(cur, "dbo.InvoiceLineItemAttachment") == 0
    assert _count(cur, "dbo.InvoiceLineItemSourceProvenance") == 0
    assert _count(cur, "dbo.InvoiceAttachment") == 0
    assert _count(cur, "dbo.Review") == 0
    assert _count(cur, "dbo.Attachment") == 1


def test_invoice_non_draft_refused(conn):
    cur = conn.cursor()
    inv_id = _seed_invoice_with_children(cur, is_draft=0, invoice_number="INV-ND")
    before = _invoice_child_counts(cur)
    conn.commit()
    err = _expect_raiserror(cur, "DeleteInvoiceCascadeById", inv_id)
    assert "STATUS_LOCKED" in err
    _assert_counts_unchanged(cur, before)


def test_invoice_qbo_refused(conn):
    cur = conn.cursor()
    inv_id = _seed_invoice_with_children(cur, is_draft=1, qbo_id="88", invoice_number="INV-Q")
    before = _invoice_child_counts(cur)
    conn.commit()
    err = _expect_raiserror(cur, "DeleteInvoiceCascadeById", inv_id)
    assert "QuickBooks" in err
    _assert_counts_unchanged(cur, before)


def test_invoice_missing_id_empty_result(conn):
    cur = conn.cursor()
    row = _call_cascade(cur, "DeleteInvoiceCascadeById", 99999)
    assert row is None


def test_bill_credit_delete_scoped_to_target(conn):
    cur = conn.cursor()
    sibling_id = _seed_bill_credit_with_children(cur, is_draft=1, credit_number="CR-SIB")
    target_id = _seed_bill_credit_with_children(cur, is_draft=1, credit_number="CR-TGT")
    conn.commit()
    _call_cascade(cur, "DeleteBillCreditCascadeById", target_id)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM dbo.BillCredit WHERE Id = ?", sibling_id)
    assert int(cur.fetchone()[0]) == 1
    cur.execute(
        "SELECT COUNT(*) FROM dbo.BillCreditLineItem WHERE BillCreditId = ?",
        sibling_id,
    )
    assert int(cur.fetchone()[0]) == 1
    cur.execute(
        """
        SELECT COUNT(*) FROM dbo.BillCreditLineItemAttachment bclia
        INNER JOIN dbo.BillCreditLineItem bcli ON bcli.Id = bclia.BillCreditLineItemId
        WHERE bcli.BillCreditId = ?
        """,
        sibling_id,
    )
    assert int(cur.fetchone()[0]) == 1
    cur.execute("SELECT COUNT(*) FROM dbo.Review WHERE BillCreditId = ?", sibling_id)
    assert int(cur.fetchone()[0]) == 1


def test_invoice_delete_scoped_to_target(conn):
    cur = conn.cursor()
    sibling_id = _seed_invoice_with_children(cur, is_draft=1, invoice_number="INV-SIB")
    target_id = _seed_invoice_with_children(cur, is_draft=1, invoice_number="INV-TGT")
    conn.commit()
    _call_cascade(cur, "DeleteInvoiceCascadeById", target_id)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM dbo.Invoice WHERE Id = ?", sibling_id)
    assert int(cur.fetchone()[0]) == 1
    cur.execute(
        "SELECT COUNT(*) FROM dbo.InvoiceLineItem WHERE InvoiceId = ?",
        sibling_id,
    )
    assert int(cur.fetchone()[0]) == 1
    cur.execute(
        """
        SELECT COUNT(*) FROM dbo.InvoiceLineItemAttachment ilia
        INNER JOIN dbo.InvoiceLineItem ili ON ili.Id = ilia.InvoiceLineItemId
        WHERE ili.InvoiceId = ?
        """,
        sibling_id,
    )
    assert int(cur.fetchone()[0]) == 1
    cur.execute(
        """
        SELECT COUNT(*) FROM dbo.InvoiceLineItemSourceProvenance prov
        INNER JOIN dbo.InvoiceLineItem ili ON ili.Id = prov.InvoiceLineItemId
        WHERE ili.InvoiceId = ?
        """,
        sibling_id,
    )
    assert int(cur.fetchone()[0]) == 1
    cur.execute(
        "SELECT COUNT(*) FROM dbo.InvoiceAttachment WHERE InvoiceId = ?",
        sibling_id,
    )
    assert int(cur.fetchone()[0]) == 1
    cur.execute("SELECT COUNT(*) FROM dbo.Review WHERE InvoiceId = ?", sibling_id)
    assert int(cur.fetchone()[0]) == 1


def test_bill_credit_xact_abort_leaves_nothing_deleted_on_fk_block(conn):
    cur = conn.cursor()
    bc_id = _seed_bill_credit_with_children(cur, is_draft=1, credit_number="CR-XA")
    cur.execute("INSERT INTO dbo._U684aBillCreditBlocker (BillCreditId) VALUES (?);", bc_id)
    before = _bill_credit_child_counts(cur)
    conn.commit()
    # autocommit ON: no outer transaction for a caller-side rollback to hide behind, so
    # only the sproc's own XACT_ABORT can undo the deletes made before the blocked one.
    conn.autocommit = True
    try:
        with pytest.raises(pyodbc.Error):
            _call_cascade(cur, "DeleteBillCreditCascadeById", bc_id)
        _assert_counts_unchanged(cur, before)
    finally:
        conn.autocommit = False


def test_invoice_xact_abort_leaves_nothing_deleted_on_fk_block(conn):
    cur = conn.cursor()
    inv_id = _seed_invoice_with_children(cur, is_draft=1, invoice_number="INV-XA")
    cur.execute("INSERT INTO dbo._U684aInvoiceBlocker (InvoiceId) VALUES (?);", inv_id)
    before = _invoice_child_counts(cur)
    conn.commit()
    # autocommit ON: no outer transaction for a caller-side rollback to hide behind, so
    # only the sproc's own XACT_ABORT can undo the deletes made before the blocked one.
    conn.autocommit = True
    try:
        with pytest.raises(pyodbc.Error):
            _call_cascade(cur, "DeleteInvoiceCascadeById", inv_id)
        _assert_counts_unchanged(cur, before)
    finally:
        conn.autocommit = False
