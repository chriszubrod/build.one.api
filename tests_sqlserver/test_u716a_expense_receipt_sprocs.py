"""U-716a: ReadExpenseReceiptCandidates + AttachFirstReceiptToExpense on local SQL Server."""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
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
MATCH_SQL = REPO_ROOT / "entities/expense/sql/dbo.expense_receipt_match.sql"
ELIA_SQL = REPO_ROOT / "entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql"

USER_ACCESS = 716100
USER_DENIED = 716200
ADMIN_FLAG = 1


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


def _exec_file(cur, path: Path) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        cur.execute(batch)


def _drain(cur) -> None:
    while True:
        if cur.description:
            cur.fetchall()
        if not cur.nextset():
            break


def _call_candidates(cur, *, amount, exp_date, vendor_id, actor, admin):
    cur.execute("SET NOCOUNT ON;")
    _drain(cur)
    cur.execute(
        """
        EXEC dbo.ReadExpenseReceiptCandidates
            @TotalAmount = ?,
            @ExpenseDate = ?,
            @VendorId = ?,
            @ActorUserId = ?,
            @ActorIsSystemAdmin = ?
        """,
        amount,
        exp_date,
        vendor_id,
        actor,
        admin,
    )
    rows = cur.fetchall() if cur.description else []
    _drain(cur)
    return rows


def _call_attach(cur, *, expense_pub, attachment_id, actor, admin, created_by=17):
    cur.execute("SET NOCOUNT ON;")
    _drain(cur)
    cur.execute(
        """
        EXEC dbo.AttachFirstReceiptToExpense
            @ExpensePublicId = ?,
            @AttachmentId = ?,
            @CreatedByUserId = ?,
            @ActorUserId = ?,
            @ActorIsSystemAdmin = ?
        """,
        expense_pub,
        attachment_id,
        created_by,
        actor,
        admin,
    )
    row = cur.fetchone() if cur.description else None
    _drain(cur)
    return row


def _drop_schema(cur) -> None:
    for name in (
        "ReadExpenseReceiptCandidates",
        "AttachFirstReceiptToExpense",
        "UserCanAccessExpense",
    ):
        cur.execute(f"IF OBJECT_ID('dbo.{name}', 'P') IS NOT NULL DROP PROCEDURE dbo.{name}")
        cur.execute(f"IF OBJECT_ID('dbo.{name}', 'FN') IS NOT NULL DROP FUNCTION dbo.{name}")
    for tbl in (
        "dbo.BillLineItemAttachment",
        "dbo.ExpenseLineItemAttachment",
        "dbo.ExpenseLineItem",
        "dbo.Expense",
        "dbo.UserProject",
        "dbo.Vendor",
        "dbo.Attachment",
        "dbo.Project",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")
    for tbl in ("ms.DriveItemAttachment", "box.[File]"):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")
    _drain(cur)


def _install_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Vendor (
            Id BIGINT NOT NULL PRIMARY KEY,
            Name NVARCHAR(255) NOT NULL
        );
        CREATE TABLE dbo.Project (
            Id BIGINT NOT NULL PRIMARY KEY,
            Name NVARCHAR(255) NULL
        );
        CREATE TABLE dbo.Attachment (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID()
        );
        CREATE TABLE dbo.Expense (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL,
            RowVersion ROWVERSION NOT NULL,
            VendorId BIGINT NULL,
            ExpenseDate DATETIME2(3) NOT NULL,
            ReferenceNumber NVARCHAR(50) NULL,
            TotalAmount DECIMAL(18,2) NULL,
            IsDraft BIT NOT NULL DEFAULT 1,
            Status NVARCHAR(20) NULL,
            CreatedByUserId BIGINT NOT NULL DEFAULT 17
        );
        CREATE TABLE dbo.ExpenseLineItem (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL,
            RowVersion ROWVERSION NOT NULL,
            ExpenseId BIGINT NOT NULL,
            ProjectId BIGINT NULL,
            CONSTRAINT FK_ELI_Expense FOREIGN KEY (ExpenseId) REFERENCES dbo.Expense(Id)
        );
        CREATE TABLE dbo.UserProject (
            UserId BIGINT NOT NULL,
            ProjectId BIGINT NOT NULL
        );
        """
    )
    _drain(cur)
    cur.execute(
        f"""
        CREATE FUNCTION dbo.UserCanAccessExpense(
            @ActorUserId BIGINT,
            @ActorIsSystemAdmin BIT,
            @ExpenseId BIGINT
        )
        RETURNS BIT
        AS
        BEGIN
            IF @ActorIsSystemAdmin = 1 RETURN 1;
            IF EXISTS (
                SELECT 1 FROM dbo.Expense e
                WHERE e.Id = @ExpenseId AND e.CreatedByUserId = @ActorUserId
            ) RETURN 1;
            IF EXISTS (
                SELECT 1 FROM dbo.ExpenseLineItem eli
                INNER JOIN dbo.UserProject up ON up.ProjectId = eli.ProjectId
                WHERE eli.ExpenseId = @ExpenseId AND up.UserId = @ActorUserId
            ) RETURN 1;
            RETURN 0;
        END;
        """
    )
    _drain(cur)
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ms')
            EXEC('CREATE SCHEMA ms');
        IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'box')
            EXEC('CREATE SCHEMA box');
        IF OBJECT_ID('dbo.BillLineItemAttachment', 'U') IS NULL
            CREATE TABLE dbo.BillLineItemAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.BillCreditLineItemAttachment', 'U') IS NULL
            CREATE TABLE dbo.BillCreditLineItemAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.InvoiceAttachment', 'U') IS NULL
            CREATE TABLE dbo.InvoiceAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.InvoiceLineItemAttachment', 'U') IS NULL
            CREATE TABLE dbo.InvoiceLineItemAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.BusinessLicenseAttachment', 'U') IS NULL
            CREATE TABLE dbo.BusinessLicenseAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.ContractorsLicenseAttachment', 'U') IS NULL
            CREATE TABLE dbo.ContractorsLicenseAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.AssetAttachment', 'U') IS NULL
            CREATE TABLE dbo.AssetAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.TaxpayerAttachment', 'U') IS NULL
            CREATE TABLE dbo.TaxpayerAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.BillTextCodingSuggestion', 'U') IS NULL
            CREATE TABLE dbo.BillTextCodingSuggestion (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.CodingSuggestion', 'U') IS NULL
            CREATE TABLE dbo.CodingSuggestion (AttachmentId BIGINT NULL);
        IF OBJECT_ID('dbo.CertificateOfInsurance', 'U') IS NULL
            CREATE TABLE dbo.CertificateOfInsurance (AttachmentId BIGINT NULL);
        IF OBJECT_ID('ms.DriveItemAttachment', 'U') IS NULL
            CREATE TABLE ms.DriveItemAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('box.File', 'U') IS NULL
            CREATE TABLE box.[File] (AttachmentId BIGINT NULL);
        """
    )
    _drain(cur)
    _exec_file(cur, ELIA_SQL)
    _exec_file(cur, MATCH_SQL)


@pytest.fixture
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    connection = _sqlserver_connect(conn_str, autocommit=False)
    cur = connection.cursor()
    _drop_schema(cur)
    _install_schema(cur)
    connection.commit()
    yield connection
    cur = connection.cursor()
    _drop_schema(cur)
    connection.commit()
    connection.close()


def _ensure_vendor_project(cur, vendor_id=1, project_id=1) -> None:
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM dbo.Vendor WHERE Id = ?)
            INSERT INTO dbo.Vendor (Id, Name) VALUES (?, N'V');
        IF NOT EXISTS (SELECT 1 FROM dbo.Project WHERE Id = ?)
            INSERT INTO dbo.Project (Id, Name) VALUES (?, N'P');
        """,
        vendor_id,
        vendor_id,
        project_id,
        project_id,
    )
    _drain(cur)


def _seed_expense(cur, *, eid, pub, amount, exp_date, vendor_id=1, status="draft", project_id=1):
    _ensure_vendor_project(cur, vendor_id, project_id)
    cur.execute(
        """
        INSERT INTO dbo.Expense (
            Id, PublicId, VendorId, ExpenseDate, ReferenceNumber, TotalAmount, Status, CreatedByUserId
        ) VALUES (?, ?, ?, ?, N'REF', ?, ?, ?);
        INSERT INTO dbo.ExpenseLineItem (Id, PublicId, ExpenseId, ProjectId)
        VALUES (?, ?, ?, ?);
        IF NOT EXISTS (SELECT 1 FROM dbo.UserProject WHERE UserId = ? AND ProjectId = ?)
            INSERT INTO dbo.UserProject (UserId, ProjectId) VALUES (?, ?);
        """,
        eid,
        pub,
        vendor_id,
        exp_date,
        amount,
        status,
        USER_ACCESS,
        eid * 10,
        uuid.uuid4(),
        eid,
        project_id,
        USER_ACCESS,
        project_id,
        USER_ACCESS,
        project_id,
    )
    _drain(cur)


def test_candidates_exact_amount_and_date_window(conn):
    cur = conn.cursor()
    anchor = date(2026, 6, 15)
    pub_in = uuid.uuid4()
    pub_edge = uuid.uuid4()
    pub_lower_edge = uuid.uuid4()
    pub_out = uuid.uuid4()
    pub_time_in = uuid.uuid4()
    _seed_expense(cur, eid=716001, pub=pub_in, amount=Decimal("50.00"), exp_date=anchor)
    _seed_expense(cur, eid=716002, pub=pub_edge, amount=Decimal("50.00"), exp_date=anchor + timedelta(days=5))
    _seed_expense(
        cur,
        eid=716004,
        pub=pub_lower_edge,
        amount=Decimal("50.00"),
        exp_date=anchor - timedelta(days=5),
    )
    _seed_expense(cur, eid=716003, pub=pub_out, amount=Decimal("50.00"), exp_date=anchor + timedelta(days=6))
    _seed_expense(
        cur,
        eid=716005,
        pub=pub_time_in,
        amount=Decimal("50.00"),
        exp_date=datetime(2026, 6, 20, 14, 23, 0),
    )
    pub_lower_out = uuid.uuid4()
    pub_next_midnight_out = uuid.uuid4()
    _seed_expense(
        cur,
        eid=716006,
        pub=pub_lower_out,
        amount=Decimal("50.00"),
        exp_date=anchor - timedelta(days=6),
    )
    _seed_expense(
        cur,
        eid=716007,
        pub=pub_next_midnight_out,
        amount=Decimal("50.00"),
        exp_date=datetime(2026, 6, 21, 0, 0, 0),
    )
    conn.commit()

    rows = _call_candidates(
        cur,
        amount=Decimal("50.00"),
        exp_date=anchor,
        vendor_id=1,
        actor=USER_ACCESS,
        admin=0,
    )
    pubs = {str(r.PublicId).lower() for r in rows}
    assert str(pub_in).lower() in pubs
    assert str(pub_edge).lower() in pubs
    assert str(pub_lower_edge).lower() in pubs
    assert str(pub_time_in).lower() in pubs
    assert str(pub_out).lower() not in pubs
    assert str(pub_lower_out).lower() not in pubs
    assert str(pub_next_midnight_out).lower() not in pubs


def test_candidates_exclude_receipt_and_no_lines(conn):
    cur = conn.cursor()
    pub_no_lines = uuid.uuid4()
    pub_has_receipt = uuid.uuid4()
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'V')")
    cur.execute("INSERT INTO dbo.Project (Id, Name) VALUES (1, N'P')")
    cur.execute(
        """
        INSERT INTO dbo.Expense (Id, PublicId, VendorId, ExpenseDate, TotalAmount, Status, CreatedByUserId)
        VALUES (716010, ?, 1, '2026-06-01', 10.00, 'draft', ?);
        INSERT INTO dbo.Expense (Id, PublicId, VendorId, ExpenseDate, TotalAmount, Status, CreatedByUserId)
        VALUES (716011, ?, 1, '2026-06-01', 10.00, 'draft', ?);
        INSERT INTO dbo.ExpenseLineItem (Id, PublicId, ExpenseId, ProjectId)
        VALUES (716110, NEWID(), 716011, 1);
        INSERT INTO dbo.Attachment (Id, PublicId) VALUES (716900, NEWID());
        INSERT INTO dbo.ExpenseLineItemAttachment (CreatedDatetime, ModifiedDatetime, ExpenseLineItemId, AttachmentId, CreatedByUserId)
        VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 716110, 716900, 17);
        INSERT INTO dbo.UserProject (UserId, ProjectId) VALUES (?, 1);
        """,
        pub_no_lines,
        USER_ACCESS,
        pub_has_receipt,
        USER_ACCESS,
        USER_ACCESS,
    )
    conn.commit()
    rows = _call_candidates(
        cur, amount=Decimal("10.00"), exp_date=date(2026, 6, 1), vendor_id=1, actor=USER_ACCESS, admin=0
    )
    assert len(rows) == 0


def test_candidates_elia_on_non_lowest_line_excludes(conn):
    cur = conn.cursor()
    pub = uuid.uuid4()
    _ensure_vendor_project(cur)
    cur.execute(
        """
        INSERT INTO dbo.Expense (
            Id, PublicId, VendorId, ExpenseDate, TotalAmount, Status, CreatedByUserId
        ) VALUES (716012, ?, 1, '2026-06-01', 10.00, 'draft', ?);
        INSERT INTO dbo.ExpenseLineItem (Id, PublicId, ExpenseId, ProjectId) VALUES
            (716112, NEWID(), 716012, 1),
            (716113, NEWID(), 716012, 1);
        INSERT INTO dbo.Attachment (Id, PublicId) VALUES (716901, NEWID());
        INSERT INTO dbo.ExpenseLineItemAttachment (
            CreatedDatetime, ModifiedDatetime, ExpenseLineItemId, AttachmentId, CreatedByUserId
        ) VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 716113, 716901, 17);
        INSERT INTO dbo.UserProject (UserId, ProjectId) VALUES (?, 1);
        """,
        pub,
        USER_ACCESS,
        USER_ACCESS,
    )
    conn.commit()
    rows = _call_candidates(
        cur, amount=Decimal("10.00"), exp_date=date(2026, 6, 1), vendor_id=1, actor=USER_ACCESS, admin=0
    )
    assert len(rows) == 0


def test_candidates_negative_total_and_null_total(conn):
    cur = conn.cursor()
    pub_neg = uuid.uuid4()
    pub_null = uuid.uuid4()
    anchor = date(2026, 8, 1)
    _seed_expense(cur, eid=716013, pub=pub_neg, amount=Decimal("-25.00"), exp_date=anchor)
    _ensure_vendor_project(cur)
    cur.execute(
        """
        INSERT INTO dbo.Expense (
            Id, PublicId, VendorId, ExpenseDate, TotalAmount, Status, CreatedByUserId
        ) VALUES (716014, ?, 1, ?, NULL, 'draft', ?);
        INSERT INTO dbo.ExpenseLineItem (Id, PublicId, ExpenseId, ProjectId)
        VALUES (716114, NEWID(), 716014, 1);
        INSERT INTO dbo.UserProject (UserId, ProjectId) VALUES (?, 1);
        """,
        pub_null,
        anchor,
        USER_ACCESS,
        USER_ACCESS,
    )
    conn.commit()
    rows = _call_candidates(
        cur, amount=Decimal("25.00"), exp_date=anchor, vendor_id=1, actor=USER_ACCESS, admin=0
    )
    pubs = {str(r.PublicId).lower() for r in rows}
    assert str(pub_neg).lower() in pubs
    assert str(pub_null).lower() not in pubs


def test_candidates_120_day_fallback_with_today_time(conn):
    cur = conn.cursor()
    today = date.today()
    pub_today = uuid.uuid4()
    pub_old = uuid.uuid4()
    _seed_expense(
        cur,
        eid=716015,
        pub=pub_today,
        amount=Decimal("33.00"),
        exp_date=datetime(today.year, today.month, today.day, 9, 30, 0),
    )
    _seed_expense(
        cur,
        eid=716016,
        pub=pub_old,
        amount=Decimal("33.00"),
        exp_date=today - timedelta(days=121),
    )
    conn.commit()
    rows = _call_candidates(
        cur, amount=Decimal("33.00"), exp_date=None, vendor_id=1, actor=USER_ACCESS, admin=0
    )
    pubs = {str(r.PublicId).lower() for r in rows}
    assert str(pub_today).lower() in pubs
    assert str(pub_old).lower() not in pubs


def test_candidates_days_apart_order_and_id_tiebreak(conn):
    cur = conn.cursor()
    anchor = date(2026, 9, 10)
    pub_near = uuid.uuid4()
    pub_far = uuid.uuid4()
    _seed_expense(cur, eid=716017, pub=pub_near, amount=Decimal("77.00"), exp_date=anchor + timedelta(days=1))
    _seed_expense(cur, eid=716018, pub=pub_far, amount=Decimal("77.00"), exp_date=anchor + timedelta(days=4))
    conn.commit()
    rows = _call_candidates(
        cur, amount=Decimal("77.00"), exp_date=anchor, vendor_id=2, actor=USER_ACCESS, admin=0
    )
    assert len(rows) == 2
    assert int(rows[0].DaysApart) <= int(rows[1].DaysApart)
    assert rows[0].DaysApart == 1
    assert rows[1].DaysApart == 4

    # Equal DaysApart: the higher Id comes first.
    pub_low_id = uuid.uuid4()
    pub_high_id = uuid.uuid4()
    _seed_expense(cur, eid=716030, pub=pub_low_id, amount=Decimal("78.00"), exp_date=anchor + timedelta(days=2))
    _seed_expense(cur, eid=716031, pub=pub_high_id, amount=Decimal("78.00"), exp_date=anchor - timedelta(days=2))
    conn.commit()
    tie = _call_candidates(
        cur, amount=Decimal("78.00"), exp_date=anchor, vendor_id=2, actor=USER_ACCESS, admin=0
    )
    assert [str(r.PublicId).lower() for r in tie] == [str(pub_high_id).lower(), str(pub_low_id).lower()]


def test_candidates_actor_scoping_and_admin(conn):
    cur = conn.cursor()
    pub = uuid.uuid4()
    _seed_expense(cur, eid=716020, pub=pub, amount=Decimal("20.00"), exp_date=date(2026, 5, 1))
    conn.commit()
    denied = _call_candidates(
        cur, amount=Decimal("20.00"), exp_date=date(2026, 5, 1), vendor_id=1, actor=USER_DENIED, admin=0
    )
    assert len(denied) == 0
    admin_rows = _call_candidates(
        cur, amount=Decimal("20.00"), exp_date=date(2026, 5, 1), vendor_id=1, actor=USER_DENIED, admin=ADMIN_FLAG
    )
    assert len(admin_rows) == 1

    null_actor = _call_candidates(
        cur, amount=Decimal("20.00"), exp_date=date(2026, 5, 1), vendor_id=1, actor=None, admin=0
    )
    assert len(null_actor) == 0


def test_candidates_vendor_ordering_and_top10(conn):
    cur = conn.cursor()
    anchor = date(2026, 7, 1)
    for i in range(12):
        _seed_expense(
            cur,
            eid=716100 + i,
            pub=uuid.uuid4(),
            amount=Decimal("99.00"),
            exp_date=anchor,
            vendor_id=1 if i == 0 else 2,
        )
    conn.commit()
    rows = _call_candidates(
        cur, amount=Decimal("99.00"), exp_date=anchor, vendor_id=1, actor=USER_ACCESS, admin=0
    )
    assert len(rows) == 10
    assert bool(rows[0].VendorMatches) is True


def test_attach_ok_lowest_line_and_conflicts(conn):
    cur = conn.cursor()
    exp_pub = uuid.uuid4()
    att_id = 716801
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'V')")
    cur.execute("INSERT INTO dbo.Project (Id, Name) VALUES (1, N'P')")
    cur.execute(
        """
        INSERT INTO dbo.Expense (Id, PublicId, VendorId, ExpenseDate, TotalAmount, Status, CreatedByUserId)
        VALUES (716500, ?, 1, '2026-06-01', 5.00, 'completed', ?);
        INSERT INTO dbo.ExpenseLineItem (Id, PublicId, ExpenseId, ProjectId) VALUES
            (716501, '11111111-1111-1111-1111-111111111111', 716500, 1),
            (716502, '22222222-2222-2222-2222-222222222222', 716500, 1);
        INSERT INTO dbo.Attachment (Id, PublicId) VALUES (?, NEWID());
        INSERT INTO dbo.UserProject (UserId, ProjectId) VALUES (?, 1);
        """,
        exp_pub,
        USER_ACCESS,
        att_id,
        USER_ACCESS,
    )
    conn.commit()
    rv_before = cur.execute("SELECT RowVersion FROM dbo.Expense WHERE Id = 716500").fetchone()[0]
    row = _call_attach(cur, expense_pub=exp_pub, attachment_id=att_id, actor=USER_ACCESS, admin=0)
    conn.commit()
    assert row.ResultCode == "ok"
    assert str(row.ExpenseLineItemPublicId).lower() == "11111111-1111-1111-1111-111111111111"
    rv_after = cur.execute("SELECT RowVersion FROM dbo.Expense WHERE Id = 716500").fetchone()[0]
    assert rv_before == rv_after

    cur.execute("INSERT INTO dbo.Attachment (Id, PublicId) VALUES (716802, NEWID())")
    conn.commit()
    again = _call_attach(cur, expense_pub=exp_pub, attachment_id=716802, actor=USER_ACCESS, admin=0)
    assert again.ResultCode == "expense_has_receipt"


def test_attach_elia_on_higher_line_still_blocks(conn):
    cur = conn.cursor()
    exp_pub = uuid.uuid4()
    att_id = 716803
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'V')")
    cur.execute("INSERT INTO dbo.Project (Id, Name) VALUES (1, N'P')")
    cur.execute(
        """
        INSERT INTO dbo.Expense (Id, PublicId, VendorId, ExpenseDate, TotalAmount, Status, CreatedByUserId)
        VALUES (716505, ?, 1, '2026-06-01', 5.00, 'draft', ?);
        INSERT INTO dbo.ExpenseLineItem (Id, PublicId, ExpenseId, ProjectId) VALUES
            (716503, NEWID(), 716505, 1),
            (716504, NEWID(), 716505, 1);
        INSERT INTO dbo.Attachment (Id, PublicId) VALUES (716899, NEWID());
        INSERT INTO dbo.ExpenseLineItemAttachment (
            CreatedDatetime, ModifiedDatetime, ExpenseLineItemId, AttachmentId, CreatedByUserId
        ) VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 716504, 716899, 17);
        INSERT INTO dbo.Attachment (Id, PublicId) VALUES (?, NEWID());
        INSERT INTO dbo.UserProject (UserId, ProjectId) VALUES (?, 1);
        """,
        exp_pub,
        USER_ACCESS,
        att_id,
        USER_ACCESS,
    )
    conn.commit()
    row = _call_attach(cur, expense_pub=exp_pub, attachment_id=att_id, actor=USER_ACCESS, admin=0)
    assert row.ResultCode == "expense_has_receipt"


def test_attach_inaccessible_expense_not_found(conn):
    cur = conn.cursor()
    exp_pub = uuid.uuid4()
    att_id = 716804
    _seed_expense(cur, eid=716506, pub=exp_pub, amount=Decimal("1.00"), exp_date=date(2026, 6, 3))
    cur.execute("INSERT INTO dbo.Attachment (Id, PublicId) VALUES (?, NEWID())", att_id)
    conn.commit()
    denied = _call_attach(cur, expense_pub=exp_pub, attachment_id=att_id, actor=USER_DENIED, admin=0)
    assert denied.ResultCode == "not_found"
    cand = _call_candidates(
        cur,
        amount=Decimal("1.00"),
        exp_date=date(2026, 6, 3),
        vendor_id=1,
        actor=USER_DENIED,
        admin=0,
    )
    assert len(cand) == 0


def test_attach_bill_line_item_link_blocks(conn):
    cur = conn.cursor()
    exp_pub = uuid.uuid4()
    att_id = 716805
    _seed_expense(cur, eid=716507, pub=exp_pub, amount=Decimal("2.00"), exp_date=date(2026, 6, 4))
    cur.execute(
        """
        INSERT INTO dbo.Attachment (Id, PublicId) VALUES (?, NEWID());
        INSERT INTO dbo.BillLineItemAttachment (AttachmentId) VALUES (?);
        """,
        att_id,
        att_id,
    )
    conn.commit()
    row = _call_attach(cur, expense_pub=exp_pub, attachment_id=att_id, actor=USER_ACCESS, admin=0)
    assert row.ResultCode == "attachment_linked"


def test_attach_attachment_linked_and_no_lines_and_not_found(conn):
    cur = conn.cursor()
    exp_pub = uuid.uuid4()
    att_id = 716901
    cur.execute(
        """
        INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'V');
        INSERT INTO dbo.Project (Id, Name) VALUES (1, N'P');
        INSERT INTO dbo.Expense (Id, PublicId, VendorId, ExpenseDate, TotalAmount, Status, CreatedByUserId)
        VALUES (716600, ?, 1, '2026-06-01', 1.00, 'draft', ?);
        INSERT INTO dbo.Attachment (Id, PublicId) VALUES (?, NEWID());
        INSERT INTO dbo.UserProject (UserId, ProjectId) VALUES (?, 1);
        """,
        exp_pub,
        USER_ACCESS,
        att_id,
        USER_ACCESS,
    )
    conn.commit()
    no_lines = _call_attach(cur, expense_pub=exp_pub, attachment_id=att_id, actor=USER_ACCESS, admin=0)
    assert no_lines.ResultCode == "expense_has_no_lines"

    cur.execute(
        """
        INSERT INTO dbo.ExpenseLineItem (Id, PublicId, ExpenseId, ProjectId) VALUES (716601, NEWID(), 716600, 1);
        """
    )
    conn.commit()
    ok = _call_attach(cur, expense_pub=exp_pub, attachment_id=att_id, actor=USER_ACCESS, admin=0)
    conn.commit()
    assert ok.ResultCode == "ok"

    exp2 = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.Expense (Id, PublicId, VendorId, ExpenseDate, TotalAmount, Status, CreatedByUserId)
        VALUES (716601, ?, 1, '2026-06-02', 1.00, 'draft', ?);
        INSERT INTO dbo.ExpenseLineItem (Id, PublicId, ExpenseId, ProjectId) VALUES (716602, NEWID(), 716601, 1);
        """,
        exp2,
        USER_ACCESS,
    )
    conn.commit()
    linked = _call_attach(cur, expense_pub=exp2, attachment_id=att_id, actor=USER_ACCESS, admin=0)
    assert linked.ResultCode == "attachment_linked"

    cur.execute("INSERT INTO dbo.Attachment (Id, PublicId) VALUES (716903, NEWID())")
    conn.commit()
    missing = _call_attach(
        cur, expense_pub=uuid.uuid4(), attachment_id=716903, actor=USER_DENIED, admin=0
    )
    assert missing.ResultCode == "not_found"

    null_actor = _call_attach(
        cur, expense_pub=exp_pub, attachment_id=716903, actor=None, admin=0
    )
    assert null_actor.ResultCode == "not_found"
