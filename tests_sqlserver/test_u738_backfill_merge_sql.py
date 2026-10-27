"""U-738: backfill merge SQL on buildone_test (keeps newer row, real ELI sprocs)."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pyodbc
import pytest

from scripts.backfill_merge_recode_leftover_expense_lines import process_expense

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}
REPO_ROOT = Path(__file__).resolve().parents[1]
EXPENSE_LINE_ITEM_SQL = REPO_ROOT / "entities/expense_line_item/sql/dbo.expense_line_item.sql"


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


def _drain(cur):
    while True:
        if cur.description:
            cur.fetchall()
        if not cur.nextset():
            break


def _drop_all(cur) -> None:
    for proc in (
        "DeleteExpenseLineItemById",
        "UpdateExpenseLineItemById",
        "SetExpenseLineItemQboIdentity",
    ):
        cur.execute(f"IF OBJECT_ID('dbo.{proc}', 'P') IS NOT NULL DROP PROCEDURE dbo.{proc}")
    for tbl in (
        "dbo.InvoiceLineItem",
        "dbo.ExpenseLineItemAttachment",
        "dbo.Attachment",
        "dbo.ExpenseLineItem",
        "dbo.Expense",
        "dbo.SubCostCode",
        "dbo.Project",
        "dbo.Vendor",
        "qbo.PurchaseLine",
        "qbo.Purchase",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_minimal_parents(cur) -> None:
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'qbo')
            EXEC('CREATE SCHEMA qbo');
        CREATE TABLE dbo.Vendor (Id BIGINT NOT NULL PRIMARY KEY);
        INSERT INTO dbo.Vendor (Id) VALUES (1);
        CREATE TABLE dbo.Project (Id BIGINT NOT NULL PRIMARY KEY);
        INSERT INTO dbo.Project (Id) VALUES (1);
        CREATE TABLE dbo.Expense (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            QboId NVARCHAR(50) NULL,
            RealmId NVARCHAR(50) NULL,
            Status NVARCHAR(30) NOT NULL DEFAULT 'draft',
            ExpenseDate DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ReferenceNumber NVARCHAR(50) NOT NULL DEFAULT '',
            VendorId BIGINT NOT NULL DEFAULT 1,
            CompanyId BIGINT NOT NULL DEFAULT 1,
            CreatedByUserId BIGINT NOT NULL DEFAULT 17,
            CONSTRAINT FK_Expense_Vendor FOREIGN KEY (VendorId) REFERENCES dbo.Vendor(Id)
        );
        CREATE TABLE qbo.Purchase(
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            QboId NVARCHAR(50) NOT NULL,
            RealmId NVARCHAR(50) NOT NULL
        );
        CREATE TABLE qbo.PurchaseLine(
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            QboPurchaseId BIGINT NOT NULL,
            QboLineId NVARCHAR(50) NOT NULL
        );
        CREATE TABLE dbo.SubCostCode (
            Id BIGINT NOT NULL PRIMARY KEY,
            IsHoldingCode BIT NOT NULL DEFAULT 0
        );
        INSERT INTO dbo.SubCostCode (Id, IsHoldingCode) VALUES (99, 0);
        """
    )


def _install_schema(cur) -> None:
    _install_minimal_parents(cur)
    _exec_batches(cur, EXPENSE_LINE_ITEM_SQL)
    cur.execute(
        """
        CREATE TABLE dbo.Attachment(
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            Filename NVARCHAR(255) NOT NULL DEFAULT N'f.pdf',
            BlobUrl NVARCHAR(1024) NULL
        );
        CREATE TABLE dbo.ExpenseLineItemAttachment(
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            ExpenseLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        CREATE TABLE dbo.InvoiceLineItem(
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            ModifiedDatetime DATETIME2(3) NULL,
            ExpenseLineItemId BIGINT NULL
        );
        """
    )


@pytest.fixture(scope="module")
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = pyodbc.connect(conn_str, autocommit=False)
    name = c.cursor().execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    cur = c.cursor()
    _drop_all(cur)
    _install_schema(cur)
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _reset(conn):
    conn.rollback()
    cur = conn.cursor()
    cur.execute("DELETE FROM dbo.InvoiceLineItem")
    cur.execute("DELETE FROM dbo.ExpenseLineItemAttachment")
    cur.execute("DELETE FROM dbo.Attachment")
    cur.execute("DELETE FROM dbo.ExpenseLineItem")
    cur.execute("DELETE FROM dbo.Expense")
    cur.execute("DELETE FROM qbo.PurchaseLine")
    cur.execute("DELETE FROM qbo.Purchase")
    conn.commit()
    yield
    conn.rollback()


def _seed_merge_pair(cur, *, leftover_billed: bool = True, amounts=(50.00, 50.00)) -> tuple[int, int, int]:
    cur.execute(
        "INSERT INTO qbo.Purchase (QboId, RealmId) OUTPUT INSERTED.Id VALUES (N'P-100', N'realm-1');"
    )
    purchase_id = cur.fetchval()
    cur.execute(
        "INSERT INTO qbo.PurchaseLine (QboPurchaseId, QboLineId) VALUES (?, N'2');",
        purchase_id,
    )
    cur.execute(
        """
        INSERT INTO dbo.Expense (QboId, RealmId, Status) OUTPUT INSERTED.Id
        VALUES (N'P-100', N'realm-1', N'draft');
        """
    )
    expense_id = int(cur.fetchval())
    cur.execute(
        """
        INSERT INTO dbo.ExpenseLineItem (
            CreatedDatetime, ExpenseId, QboId, RealmId, Description, Amount, IsDraft, IsBilled
        ) OUTPUT INSERTED.Id VALUES (SYSUTCDATETIME(), ?, N'1', N'realm-1', N'leftover', ?, 0, ?);
        """,
        expense_id,
        amounts[0],
        1 if leftover_billed else 0,
    )
    leftover_id = int(cur.fetchval())
    cur.execute(
        """
        INSERT INTO dbo.ExpenseLineItem (
            CreatedDatetime, ExpenseId, QboId, RealmId, Description, Amount, SubCostCodeId, IsDraft, IsBilled
        ) OUTPUT INSERTED.Id VALUES (SYSUTCDATETIME(), ?, N'2', N'realm-1', N'newer coded', ?, 99, 0, 0);
        """,
        expense_id,
        amounts[1],
    )
    newer_id = int(cur.fetchval())
    cur.execute("INSERT INTO dbo.Attachment (Filename) OUTPUT INSERTED.Id VALUES (N'receipt.pdf');")
    attachment_id = int(cur.fetchval())
    cur.execute(
        """
        INSERT INTO dbo.ExpenseLineItemAttachment (ExpenseLineItemId, AttachmentId)
        VALUES (?, ?);
        """,
        leftover_id,
        attachment_id,
    )
    cur.execute("INSERT INTO dbo.InvoiceLineItem (ExpenseLineItemId) VALUES (?);", leftover_id)
    return expense_id, leftover_id, newer_id


def test_dry_run_writes_nothing(conn):
    cur = conn.cursor()
    expense_id, leftover_id, newer_id = _seed_merge_pair(cur)
    conn.commit()

    outcome, _, _ = process_expense(
        cur,
        expense_id,
        apply=False,
        holding_scc_ids=frozenset(),
        workbook_public_ids=frozenset(),
    )
    assert outcome == "would_merge"
    assert cur.execute("SELECT COUNT(*) FROM dbo.ExpenseLineItem").fetchval() == 2
    assert cur.execute(
        "SELECT ExpenseLineItemId FROM dbo.ExpenseLineItemAttachment"
    ).fetchval() == leftover_id


def test_apply_merge_keeps_newer_row_and_moves_links(conn):
    cur = conn.cursor()
    expense_id, leftover_id, newer_id = _seed_merge_pair(cur)
    conn.commit()

    outcome, _, _ = process_expense(
        cur,
        expense_id,
        apply=True,
        holding_scc_ids=frozenset(),
        workbook_public_ids=frozenset(),
    )
    conn.commit()
    assert outcome == "merged"

    assert cur.execute("SELECT COUNT(*) FROM dbo.ExpenseLineItem").fetchval() == 1
    row = cur.execute(
        "SELECT Id, QboId, SubCostCodeId, IsBilled FROM dbo.ExpenseLineItem"
    ).fetchone()
    assert int(row.Id) == newer_id
    assert row.QboId == "2"
    assert int(row.SubCostCodeId) == 99
    assert row.IsBilled is True
    assert cur.execute(
        "SELECT ExpenseLineItemId FROM dbo.ExpenseLineItemAttachment"
    ).fetchval() == newer_id
    assert cur.execute(
        "SELECT ExpenseLineItemId FROM dbo.InvoiceLineItem"
    ).fetchval() == newer_id
    assert cur.execute(
        "SELECT COUNT(*) FROM dbo.ExpenseLineItem WHERE Id = ?", leftover_id
    ).fetchval() == 0


def test_amount_mismatch_flags_without_write(conn):
    cur = conn.cursor()
    expense_id, _, _ = _seed_merge_pair(cur, amounts=(50.00, 60.00))
    conn.commit()

    outcome, reason, _ = process_expense(
        cur,
        expense_id,
        apply=True,
        holding_scc_ids=frozenset(),
        workbook_public_ids=frozenset(),
    )
    conn.rollback()
    assert outcome == "flag_manual:amount_mismatch"
    assert reason == "flag_manual:amount_mismatch"
    assert cur.execute("SELECT COUNT(*) FROM dbo.ExpenseLineItem").fetchval() == 2


def test_both_attachments_flags_without_write(conn):
    cur = conn.cursor()
    expense_id, leftover_id, newer_id = _seed_merge_pair(cur)
    cur.execute("INSERT INTO dbo.Attachment (Filename) OUTPUT INSERTED.Id VALUES (N'b.pdf');")
    att2 = int(cur.fetchval())
    cur.execute(
        "INSERT INTO dbo.ExpenseLineItemAttachment (ExpenseLineItemId, AttachmentId) VALUES (?, ?);",
        newer_id,
        att2,
    )
    conn.commit()

    outcome, reason, _ = process_expense(
        cur,
        expense_id,
        apply=True,
        holding_scc_ids=frozenset(),
        workbook_public_ids=frozenset(),
    )
    conn.rollback()
    assert outcome == "flag_manual:both_have_attachments"
    assert cur.execute("SELECT COUNT(*) FROM dbo.ExpenseLineItem").fetchval() == 2


def test_same_attachment_on_both_lines_merges_and_keeps_receipt(conn):
    """QBO re-attached the SAME receipt to the new line: the leftover's link is a
    duplicate - merge drops it, the receipt stays linked to the kept line (U-738 EM)."""
    cur = conn.cursor()
    expense_id, leftover_id, newer_id = _seed_merge_pair(cur)
    attachment_id = cur.execute(
        "SELECT AttachmentId FROM dbo.ExpenseLineItemAttachment WHERE ExpenseLineItemId = ?", leftover_id
    ).fetchval()
    cur.execute(
        "INSERT INTO dbo.ExpenseLineItemAttachment (ExpenseLineItemId, AttachmentId) VALUES (?, ?);",
        newer_id,
        attachment_id,
    )
    conn.commit()

    outcome, _, _ = process_expense(
        cur,
        expense_id,
        apply=True,
        holding_scc_ids=frozenset(),
        workbook_public_ids=frozenset(),
    )
    conn.commit()
    assert outcome == "merged"
    assert cur.execute("SELECT COUNT(*) FROM dbo.ExpenseLineItem WHERE Id = ?", leftover_id).fetchval() == 0
    links = cur.execute("SELECT ExpenseLineItemId, AttachmentId FROM dbo.ExpenseLineItemAttachment").fetchall()
    assert [(int(r[0]), int(r[1])) for r in links] == [(newer_id, int(attachment_id))]
    assert cur.execute("SELECT COUNT(*) FROM dbo.Attachment WHERE Id = ?", attachment_id).fetchval() == 1
