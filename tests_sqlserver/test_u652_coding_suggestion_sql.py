"""U-652: CodingSuggestion SQL executed against a real SQL Server."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pyodbc
import pytest


def _sqlserver_connect(conn_str: str, *, autocommit: bool = False):
    """Run this suite in its own pytest invocation (see tests_sqlserver/README.md): tests/conftest.py
    replaces pyodbc.connect for the unit suite, and that guard is never worked around here."""
    return pyodbc.connect(conn_str, autocommit=autocommit)

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


if os.environ.get("BUILDONE_TEST_SQLSERVER") and _server_host(os.environ["BUILDONE_TEST_SQLSERVER"]) not in _LOCAL_SERVERS:
    raise RuntimeError("tests_sqlserver refuses to run: SERVER in BUILDONE_TEST_SQLSERVER must be 127.0.0.1 or localhost")

REPO_ROOT = Path(__file__).resolve().parents[1]
CODING_SUGGESTION_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
MIGRATION_SQL = REPO_ROOT / "scripts/migrations/u652_coding_suggestion.sql"
QUEUE_SQL = REPO_ROOT / "integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql"
LEGACY_ECI_SQL = REPO_ROOT / "tests_sqlserver/fixtures/legacy_expense_coding_item.sql"


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


def _drop_coding_artifacts(cur) -> None:
    for obj in (
        "dbo.MarkCodingSuggestionResolvedExternally",
        "dbo.ReadExternallyResolvedCodingSuggestionCandidates",
        "dbo.ReadCodingSuggestionExistsForPurchase",
        "dbo.ReadCodingSuggestionStateByExpenseIds",
        "dbo.MarkCodingSuggestionError",
        "dbo.MarkCodingSuggestionChangedInQbo",
        "dbo.MarkCodingSuggestionWritten",
        "dbo.MarkCodingSuggestionEnqueued",
        "dbo.RecordCodingSuggestionConfirmation",
        "dbo.ReleaseCodingSuggestion",
        "dbo.ClaimCodingSuggestion",
        "dbo.RecordCodingSuggestionFlag",
        "dbo.RecordCodingSuggestion",
        "dbo.ReadCodingSuggestionByPublicId",
        "dbo.UpsertCodingSuggestionForPurchase",
        "dbo.UserCanAccessProject",
    ):
        kind = "P" if "UserCanAccess" not in obj else "FN"
        cur.execute(f"IF OBJECT_ID('{obj}', '{kind}') IS NOT NULL DROP {'FUNCTION' if kind == 'FN' else 'PROCEDURE'} {obj}")
    for tbl in (
        "dbo.ExpenseCodingItem",
        "dbo.ExpenseCodingItem_u652_backup",
        "dbo.CodingSuggestion",
        "dbo.ExpenseLineItem",
        "dbo.Expense",
        "qbo.PurchaseLine",
        "qbo.Purchase",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_supporting_schema(cur) -> None:
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'qbo')
            EXEC('CREATE SCHEMA qbo');
        """
    )
    cur.execute(
        """
        CREATE TABLE qbo.Purchase(
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            QboId NVARCHAR(50) NOT NULL,
            RealmId NVARCHAR(50) NOT NULL,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            SyncToken NVARCHAR(50) NULL,
            EntityRefValue NVARCHAR(50) NULL,
            EntityRefName NVARCHAR(255) NULL,
            Credit BIT NULL,
            TotalAmt DECIMAL(18,2) NULL,
            TxnDate NVARCHAR(50) NULL,
            DocNumber NVARCHAR(50) NULL,
            PrivateNote NVARCHAR(MAX) NULL
        );
        CREATE TABLE qbo.PurchaseLine(
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            QboPurchaseId BIGINT NOT NULL,
            QboLineId NVARCHAR(50) NOT NULL,
            LineNum INT NULL,
            Amount DECIMAL(18,2) NULL,
            Description NVARCHAR(1024) NULL,
            AccountRefName NVARCHAR(255) NULL,
            ItemRefValue NVARCHAR(50) NULL
        );
        CREATE TABLE dbo.Expense(
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            QboId NVARCHAR(50) NULL,
            RealmId NVARCHAR(50) NULL,
            Status NVARCHAR(30) NOT NULL DEFAULT 'completed',
            StatusOrigin NVARCHAR(30) NULL,
            ExpenseDate DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
            TotalAmount DECIMAL(18,2) NULL,
            ReferenceNumber NVARCHAR(50) NOT NULL DEFAULT '',
            VendorId BIGINT NOT NULL DEFAULT 1
        );
        CREATE TABLE dbo.ExpenseLineItem(
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            ExpenseId BIGINT NOT NULL,
            QboId NVARCHAR(50) NULL,
            RealmId NVARCHAR(50) NULL
        );
        """
    )
    cur.execute(
        """
        CREATE OR ALTER FUNCTION dbo.UserCanAccessProject(
            @ActorUserId BIGINT,
            @ActorIsSystemAdmin BIT,
            @ProjectId BIGINT
        )
        RETURNS BIT
        AS
        BEGIN
            RETURN 1;
        END;
        """
    )
    _exec_batches(cur, CODING_SUGGESTION_SQL)
    _exec_batches(cur, QUEUE_SQL)


@pytest.fixture(scope="module")
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=False)
    name = c.cursor().execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    rcsi = c.cursor().execute(
        "SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = DB_NAME()"
    ).fetchval()
    assert rcsi == 1, "buildone_test must run READ_COMMITTED_SNAPSHOT ON"
    cur = c.cursor()
    _drop_coding_artifacts(cur)
    _install_supporting_schema(cur)
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _reset(conn):
    conn.rollback()
    cur = conn.cursor()
    cur.execute("DELETE FROM dbo.ExpenseLineItem")
    cur.execute("DELETE FROM dbo.CodingSuggestion")
    cur.execute("IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NOT NULL DROP TABLE dbo.ExpenseCodingItem")
    cur.execute(
        "IF OBJECT_ID('dbo.ExpenseCodingItem_u652_backup', 'U') IS NOT NULL "
        "DROP TABLE dbo.ExpenseCodingItem_u652_backup"
    )
    cur.execute("DELETE FROM qbo.PurchaseLine")
    cur.execute("DELETE FROM dbo.Expense")
    cur.execute("DELETE FROM qbo.Purchase")
    conn.commit()
    yield
    conn.rollback()


def _insert_expense_purchase(
    cur,
    *,
    expense_id: int | None = None,
    qbo_purchase_qbo_id: str = "P-100",
    realm_id: str = "realm-1",
    qbo_line_id: str = "1",
    uncategorized: bool = True,
) -> tuple[int, int, int]:
    cur.execute(
        """
        INSERT INTO qbo.Purchase (QboId, RealmId) OUTPUT INSERTED.Id VALUES (?, ?);
        """,
        (qbo_purchase_qbo_id, realm_id),
    )
    purchase_id = cur.fetchval()
    acct = "Cost of construction : NEED TO CATEGORIZE" if uncategorized else "Materials"
    cur.execute(
        """
        INSERT INTO qbo.PurchaseLine (QboPurchaseId, QboLineId, LineNum, AccountRefName, ItemRefValue)
        OUTPUT INSERTED.Id VALUES (?, ?, 1, ?, NULL);
        """,
        (purchase_id, qbo_line_id, acct),
    )
    line_id = cur.fetchval()
    if expense_id is None:
        cur.execute(
            """
            INSERT INTO dbo.Expense (QboId, RealmId, Status, StatusOrigin, ReferenceNumber)
            OUTPUT INSERTED.Id VALUES (?, ?, N'completed', N'qbo_pull', N'REF');
            """,
            (qbo_purchase_qbo_id, realm_id),
        )
        expense_id = cur.fetchval()
    cur.execute(
        "INSERT INTO dbo.ExpenseLineItem (ExpenseId, QboId, RealmId) VALUES (?, ?, ?);",
        (expense_id, qbo_line_id, realm_id),
    )
    return purchase_id, line_id, expense_id


def test_check_accepts_three_valid_target_shapes(conn):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (TargetKind, BillId, Status)
        VALUES (N'bill', 1, N'pending');
        """
    )
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (TargetKind, BillCreditId, Status)
        VALUES (N'bill_credit', 2, N'pending');
        """
    )
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (TargetKind, ExpenseId, Status)
        VALUES (N'expense', 3, N'pending');
        """
    )
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (TargetKind, QboPurchaseQboId, RealmId, Status)
        VALUES (N'expense', N'P-x', N'r1', N'pending');
        """
    )
    conn.commit()
    assert cur.execute("SELECT COUNT(*) FROM dbo.CodingSuggestion").fetchval() == 4


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO dbo.CodingSuggestion (TargetKind, BillId, ExpenseId, Status) VALUES (N'bill', 1, 1, N'pending')",
        "INSERT INTO dbo.CodingSuggestion (TargetKind, Status) VALUES (N'expense', N'pending')",
        "INSERT INTO dbo.CodingSuggestion (TargetKind, BillId, QboPurchaseQboId, Status) VALUES (N'bill', 1, N'P', N'pending')",
        "INSERT INTO dbo.CodingSuggestion (TargetKind, ExpenseId, QboPurchaseQboId, RealmId, Status) VALUES (N'bill', 1, N'P', N'r', N'pending')",
    ],
)
def test_check_rejects_invalid_shapes(conn, sql: str):
    cur = conn.cursor()
    with pytest.raises(pyodbc.Error):
        cur.execute(sql)
        conn.commit()
    conn.rollback()


def test_filtered_unique_indexes_reject_duplicates(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.CodingSuggestion (TargetKind, BillId, Status) VALUES (N'bill', 10, N'pending');"
    )
    with pytest.raises(pyodbc.Error):
        cur.execute(
            "INSERT INTO dbo.CodingSuggestion (TargetKind, BillId, Status) VALUES (N'bill', 10, N'pending');"
        )
        conn.commit()
    conn.rollback()

    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (TargetKind, QboPurchaseQboId, RealmId, Status)
        VALUES (N'expense', N'dup', N'r1', N'pending');
        """
    )
    with pytest.raises(pyodbc.Error):
        cur.execute(
            """
            INSERT INTO dbo.CodingSuggestion (TargetKind, QboPurchaseQboId, RealmId, Status)
            VALUES (N'expense', N'dup', N'r1', N'pending');
            """
        )
        conn.commit()
    conn.rollback()


def test_upsert_idempotent_and_preserves_status_and_confirmation(conn):
    cur = conn.cursor()
    purchase_id, _, expense_id = _insert_expense_purchase(cur)
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (
            PublicId, TargetKind, QboPurchaseId, QboPurchaseQboId, RealmId, ExpenseId,
            Status, ConfirmedProjectId, ConfirmedSubCostCodeId, SuggestedProjectId
        ) VALUES (?, N'expense', ?, N'P-100', N'realm-1', ?, N'confirmed', 5, 6, 7);
        """,
        (pub, purchase_id, expense_id),
    )
    conn.commit()

    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForPurchase @QboPurchaseId=?, @QboPurchaseQboId=N'P-100',
            @RealmId=N'realm-1', @VendorId=99, @ExpenseId=?;
        """,
        (purchase_id + 1, expense_id),
    )
    row = cur.execute(
        "SELECT Status, ConfirmedProjectId, SuggestedProjectId FROM dbo.CodingSuggestion WHERE PublicId=?",
        (pub,),
    ).fetchone()
    assert row.Status == "confirmed"
    assert row.ConfirmedProjectId == 5
    assert row.SuggestedProjectId == 7
    assert cur.execute("SELECT COUNT(*) FROM dbo.CodingSuggestion").fetchval() == 1
    conn.commit()


def test_record_confirmation_stores_confirmed_mixed_codes(conn):
    cur = conn.cursor()
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (PublicId, TargetKind, ExpenseId, Status)
        VALUES (?, N'expense', 1, N'suggested');
        """,
        (pub,),
    )
    conn.commit()
    cur.execute(
        """
        EXEC dbo.RecordCodingSuggestionConfirmation
            @PublicId=?,
            @ConfirmedProjectId=1,
            @ConfirmedSubCostCodeId=2,
            @ConfirmedMixedCodes=1,
            @PrefillState=N'accepted',
            @Status=N'confirmed';
        """,
        (pub,),
    )
    row = cur.execute(
        "SELECT ConfirmedMixedCodes, PrefillState, Status FROM dbo.CodingSuggestion WHERE PublicId=?",
        (pub,),
    ).fetchone()
    assert row.ConfirmedMixedCodes is True
    assert row.PrefillState == "accepted"
    assert row.Status == "confirmed"
    conn.commit()


def test_read_state_one_suggestion_two_lines_returns_one_row(conn):
    cur = conn.cursor()
    purchase_id, _, expense_id = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-2L")
    cur.execute(
        """
        INSERT INTO qbo.PurchaseLine (QboPurchaseId, QboLineId, LineNum, AccountRefName, ItemRefValue)
        VALUES (?, N'2', 2, N'Cost of construction : NEED TO CATEGORIZE', NULL);
        """,
        (purchase_id,),
    )
    cur.execute(
        """
        INSERT INTO dbo.ExpenseLineItem (ExpenseId, QboId, RealmId) VALUES (?, N'2', N'realm-1');
        """,
        (expense_id,),
    )
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (
            PublicId, TargetKind, ExpenseId, QboPurchaseId, QboPurchaseQboId, RealmId, Status
        ) VALUES (?, N'expense', ?, ?, N'P-2L', N'realm-1', N'suggested');
        """,
        (pub, expense_id, purchase_id),
    )
    conn.commit()
    cur.execute(
        """
        EXEC dbo.ReadCodingSuggestionStateByExpenseIds @ExpenseIds=?, @ActorIsSystemAdmin=1;
        """,
        (str(expense_id),),
    )
    rows = cur.fetchall()
    assert len(rows) == 1
    from entities.expense.api.router import build_expense_coding_block

    block = build_expense_coding_block(
        [{"public_id": str(pub), "status": rows[0].Status}]
    )
    assert block["open_items"] == 1
    conn.commit()


def test_read_state_by_expense_id_and_purchase_fallback(conn):
    cur = conn.cursor()
    purchase_id, _, expense_id = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-200")
    pub_direct = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (
            PublicId, TargetKind, ExpenseId, QboPurchaseId, QboPurchaseQboId, RealmId, Status, SuggestionConfidence
        ) VALUES (?, N'expense', ?, ?, N'P-200', N'realm-1', N'suggested', 0.9);
        """,
        (pub_direct, expense_id, purchase_id),
    )

    purchase_id2, _, expense_id2 = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-201")
    pub_fallback = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (
            PublicId, TargetKind, ExpenseId, QboPurchaseId, QboPurchaseQboId, RealmId, Status
        ) VALUES (?, N'expense', NULL, ?, N'P-201', N'realm-1', N'flagged');
        """,
        (pub_fallback, purchase_id2),
    )
    conn.commit()

    cur.execute(
        """
        EXEC dbo.ReadCodingSuggestionStateByExpenseIds @ExpenseIds=?, @ActorIsSystemAdmin=1;
        """,
        (f"{expense_id},{expense_id2}",),
    )
    rows = {r.ExpenseId: r for r in cur.fetchall()}
    assert str(rows[expense_id].CodingItemPublicId).lower() == str(pub_direct).lower()
    assert str(rows[expense_id2].CodingItemPublicId).lower() == str(pub_fallback).lower()
    conn.commit()


def test_externally_resolved_candidate_is_per_purchase(conn):
    cur = conn.cursor()
    purchase_id, _, _ = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-300", uncategorized=False)
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (
            PublicId, TargetKind, QboPurchaseId, QboPurchaseQboId, RealmId, Status
        ) VALUES (?, N'expense', ?, N'P-300', N'realm-1', N'pending');
        """,
        (pub, purchase_id),
    )
    conn.commit()
    cur.execute("EXEC dbo.ReadExternallyResolvedCodingSuggestionCandidates;")
    pubs = {str(r.PublicId).lower() for r in cur.fetchall()}
    assert str(pub).lower() in pubs

    # still one uncategorized line on purchase => not a candidate
    cur.execute(
        """
        INSERT INTO qbo.PurchaseLine (QboPurchaseId, QboLineId, LineNum, AccountRefName, ItemRefValue)
        VALUES (?, N'2', 2, N'Cost of construction : NEED TO CATEGORIZE', NULL);
        """,
        (purchase_id,),
    )
    conn.commit()
    cur.execute("EXEC dbo.ReadExternallyResolvedCodingSuggestionCandidates;")
    pubs2 = {str(r.PublicId).lower() for r in cur.fetchall()}
    assert str(pub).lower() not in pubs2
    conn.commit()


def test_externally_resolved_item_recode_counts_only_when_no_genuine_placeholder_remains(conn):
    """A line keeping the 58999 label but carrying an Item was recoded in QBO (not a genuine
    placeholder). A purchase is externally resolved only when NO genuine placeholder
    (label AND no Item) remains on it — one item-recoded line must not close a purchase whose
    other line is still uncategorized."""
    cur = conn.cursor()
    purchase_id, _, _ = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-310", uncategorized=False)
    cur.execute(
        """
        INSERT INTO qbo.PurchaseLine (QboPurchaseId, QboLineId, LineNum, AccountRefName, ItemRefValue)
        VALUES (?, N'11', 11, N'Cost of construction : NEED TO CATEGORIZE', N'ITEM-7');
        """,
        (purchase_id,),
    )
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (
            PublicId, TargetKind, QboPurchaseId, QboPurchaseQboId, RealmId, Status
        ) VALUES (?, N'expense', ?, N'P-310', N'realm-1', N'pending');
        """,
        (pub, purchase_id),
    )
    conn.commit()
    cur.execute("EXEC dbo.ReadExternallyResolvedCodingSuggestionCandidates;")
    assert str(pub).lower() in {str(r.PublicId).lower() for r in cur.fetchall()}, (
        "item-recoded line with a stale label and no genuine placeholder => candidate"
    )

    cur.execute(
        """
        INSERT INTO qbo.PurchaseLine (QboPurchaseId, QboLineId, LineNum, AccountRefName, ItemRefValue)
        VALUES (?, N'12', 12, N'Cost of construction : NEED TO CATEGORIZE', NULL);
        """,
        (purchase_id,),
    )
    conn.commit()
    cur.execute("EXEC dbo.ReadExternallyResolvedCodingSuggestionCandidates;")
    assert str(pub).lower() not in {str(r.PublicId).lower() for r in cur.fetchall()}, (
        "a genuine placeholder still on the purchase => NOT externally resolved"
    )
    conn.commit()


def test_confirmation_records_and_returns_confirmation_source(conn):
    cur = conn.cursor()
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (PublicId, TargetKind, QboPurchaseQboId, RealmId, Status)
        VALUES (?, N'expense', N'P-SRC', N'realm-1', N'pending');
        """,
        (pub,),
    )
    conn.commit()
    cur.execute(
        """
        EXEC dbo.RecordCodingSuggestionConfirmation @PublicId=?, @ConfirmedProjectId=1,
             @ConfirmedSubCostCodeId=2, @ConfirmedDescription=N'd', @WasOverridden=0,
             @ConfirmedByUserId=17, @Status=N'confirmed', @ConfirmationSource=N'review_submit';
        """,
        (pub,),
    )
    cur.fetchall()
    conn.commit()
    cur.execute("EXEC dbo.ReadCodingSuggestionByPublicId @PublicId=?;", (pub,))
    row = cur.fetchone()
    assert row.ConfirmationSource == "review_submit"
    conn.commit()


def test_changed_in_qbo_mark_is_skipped_when_the_row_changed_since_it_was_read(conn):
    """A stale worker (read an endpoint confirmation) must not overwrite a newer review-submit
    confirmation that landed after its read."""
    cur = conn.cursor()
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (PublicId, TargetKind, QboPurchaseQboId, RealmId, Status)
        VALUES (?, N'expense', N'P-RV', N'realm-1', N'enqueued');
        """,
        (pub,),
    )
    conn.commit()
    stale = cur.execute("SELECT RowVersion FROM dbo.CodingSuggestion WHERE PublicId = ?", (pub,)).fetchval()
    cur.execute(
        """
        EXEC dbo.RecordCodingSuggestionConfirmation @PublicId=?, @ConfirmedProjectId=1,
             @ConfirmedSubCostCodeId=2, @Status=N'confirmed', @ConfirmationSource=N'review_submit';
        """,
        (pub,),
    )
    cur.fetchall()
    conn.commit()

    cur.execute("EXEC dbo.MarkCodingSuggestionChangedInQbo @PublicId=?, @ExpectedRowVersion=?;", (pub, stale))
    assert cur.fetchone() is None
    conn.commit()
    status, source = cur.execute(
        "SELECT Status, ConfirmationSource FROM dbo.CodingSuggestion WHERE PublicId = ?", (pub,)
    ).fetchone()
    assert (status, source) == ("confirmed", "review_submit")

    current = cur.execute("SELECT RowVersion FROM dbo.CodingSuggestion WHERE PublicId = ?", (pub,)).fetchval()
    cur.execute("EXEC dbo.MarkCodingSuggestionChangedInQbo @PublicId=?, @ExpectedRowVersion=?;", (pub, current))
    assert cur.fetchone().Status == "changed_in_qbo"
    conn.commit()


def test_queue_returns_one_row_per_purchase_with_its_uncategorized_line_count(conn):
    cur = conn.cursor()
    two_lines, _, _ = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-Q2")
    cur.execute(
        """
        INSERT INTO qbo.PurchaseLine (QboPurchaseId, QboLineId, LineNum, AccountRefName, ItemRefValue)
        VALUES (?, N'2', 2, N'Cost of construction : NEED TO CATEGORIZE', NULL);
        """,
        (two_lines,),
    )
    _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-Q1")
    conn.commit()
    cur.execute("EXEC dbo.ReadExpenseCodingQueue @ActorIsSystemAdmin=1;")
    rows = {r.QboPurchaseQboId: r for r in cur.fetchall()}
    assert set(rows) == {"P-Q2", "P-Q1"}
    assert rows["P-Q2"].UncategorizedLineCount == 2
    assert rows["P-Q1"].UncategorizedLineCount == 1
    conn.commit()


def _ensure_eci_table(cur) -> None:
    if cur.execute("SELECT OBJECT_ID('dbo.ExpenseCodingItem', 'U')").fetchval() is None:
        for batch in _batches(LEGACY_ECI_SQL.read_text(encoding="utf-8")):
            cur.execute(batch)


def _run_migration(cur, conn) -> None:
    for batch in _batches(MIGRATION_SQL.read_text(encoding="utf-8")):
        cur.execute(batch)
    conn.commit()


def test_migration_copies_rows_and_renames_backup(conn):
    cur = conn.cursor()
    _ensure_eci_table(cur)
    purchase_id, line_id, expense_id = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-M1")
    pub_mapped = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.ExpenseCodingItem (
            PublicId, QboPurchaseId, QboPurchaseLineId, QboLineId, QboPurchaseQboId, RealmId,
            Status, VendorId, CompanyId, CreatedByUserId
        ) VALUES (?, ?, ?, N'1', N'P-M1', N'realm-1', N'written', 9, 1, 17);
        """,
        (pub_mapped, purchase_id, line_id),
    )

    purchase_id2, line_id2, _ = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-M2")
    cur.execute("DELETE FROM dbo.Expense WHERE Id = (SELECT MAX(Id) FROM dbo.Expense);")
    pub_unmapped = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.ExpenseCodingItem (
            PublicId, QboPurchaseId, QboPurchaseLineId, QboLineId, QboPurchaseQboId, RealmId, Status
        ) VALUES (?, ?, ?, N'1', N'P-M2', N'realm-1', N'suggested');
        """,
        (pub_unmapped, purchase_id2, line_id2),
    )
    conn.commit()

    _run_migration(cur, conn)

    mapped = cur.execute(
        "SELECT ExpenseId, Status FROM dbo.CodingSuggestion WHERE PublicId=?",
        (pub_mapped,),
    ).fetchone()
    assert mapped.ExpenseId == expense_id
    assert mapped.Status == "written"

    unmapped = cur.execute(
        "SELECT ExpenseId, Status FROM dbo.CodingSuggestion WHERE PublicId=?",
        (pub_unmapped,),
    ).fetchone()
    assert unmapped.ExpenseId is None
    assert unmapped.Status == "suggested"

    assert cur.execute(
        "SELECT OBJECT_ID('dbo.ExpenseCodingItem_u652_backup', 'U')"
    ).fetchval() is not None
    assert cur.execute("SELECT OBJECT_ID('dbo.ExpenseCodingItem', 'U')").fetchval() is None
    assert cur.execute("SELECT OBJECT_ID('dbo.CodingSuggestion', 'U')").fetchval() is not None
    conn.commit()


def test_migration_is_noop_on_rerun(conn):
    cur = conn.cursor()
    _ensure_eci_table(cur)
    purchase_id, line_id, _ = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-R1")
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.ExpenseCodingItem (
            PublicId, QboPurchaseId, QboPurchaseLineId, QboLineId, QboPurchaseQboId, RealmId, Status
        ) VALUES (?, ?, ?, N'1', N'P-R1', N'realm-1', N'pending');
        """,
        (pub, purchase_id, line_id),
    )
    conn.commit()
    _run_migration(cur, conn)
    count1 = cur.execute("SELECT COUNT(*) FROM dbo.CodingSuggestion").fetchval()
    _run_migration(cur, conn)
    count2 = cur.execute("SELECT COUNT(*) FROM dbo.CodingSuggestion").fetchval()
    assert count1 == count2 == 1
    conn.commit()


def test_migration_throws_when_stray_proc_references_expense_coding_item(conn):
    cur = conn.cursor()
    _ensure_eci_table(cur)
    purchase_id, line_id, _ = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-STRAY")
    cur.execute(
        """
        INSERT INTO dbo.ExpenseCodingItem (
            PublicId, QboPurchaseId, QboPurchaseLineId, QboLineId, QboPurchaseQboId, RealmId, Status
        ) VALUES (NEWID(), ?, ?, N'1', N'P-STRAY', N'realm-1', N'pending');
        """,
        (purchase_id, line_id),
    )
    cur.execute(
        """
        CREATE OR ALTER PROCEDURE dbo.U652StrayExpenseCodingItemRef AS
        BEGIN
            SET NOCOUNT ON;
            IF EXISTS (SELECT 1 FROM dbo.ExpenseCodingItem) SELECT 1;
        END;
        """
    )
    conn.commit()
    with pytest.raises(pyodbc.Error):
        _run_migration(cur, conn)
    conn.rollback()
    cur.execute(
        "IF OBJECT_ID('dbo.U652StrayExpenseCodingItemRef', 'P') IS NOT NULL "
        "DROP PROCEDURE dbo.U652StrayExpenseCodingItemRef;"
    )
    conn.commit()


def _seed_one_legacy_row(cur, qbo_purchase_qbo_id: str) -> None:
    _ensure_eci_table(cur)
    purchase_id, line_id, _ = _insert_expense_purchase(cur, qbo_purchase_qbo_id=qbo_purchase_qbo_id)
    cur.execute(
        """
        INSERT INTO dbo.ExpenseCodingItem (
            PublicId, QboPurchaseId, QboPurchaseLineId, QboLineId, QboPurchaseQboId, RealmId, Status
        ) VALUES (NEWID(), ?, ?, N'1', ?, N'realm-1', N'pending');
        """,
        (purchase_id, line_id, qbo_purchase_qbo_id),
    )


def test_migration_ignores_a_comment_only_mention(conn):
    """A module that only MENTIONS the old table in a comment is not a dependency."""
    cur = conn.cursor()
    _seed_one_legacy_row(cur, "P-CMT")
    cur.execute(
        """
        CREATE OR ALTER PROCEDURE dbo.U652CommentOnly AS
        BEGIN
            -- ExpenseCodingItem was migrated to CodingSuggestion in U-652.
            SET NOCOUNT ON;
            SELECT 1;
        END;
        """
    )
    conn.commit()
    try:
        _run_migration(cur, conn)
        assert cur.execute("SELECT OBJECT_ID('dbo.ExpenseCodingItem_u652_backup', 'U')").fetchval() is not None
    finally:
        conn.rollback()
        cur.execute("IF OBJECT_ID('dbo.U652CommentOnly', 'P') IS NOT NULL DROP PROCEDURE dbo.U652CommentOnly;")
        conn.commit()


def test_migration_throws_when_a_trigger_references_the_old_table(conn):
    cur = conn.cursor()
    _seed_one_legacy_row(cur, "P-TRG")
    cur.execute(
        """
        CREATE OR ALTER TRIGGER qbo.U652StrayTrigger ON qbo.Purchase AFTER INSERT AS
        BEGIN
            SET NOCOUNT ON;
            IF EXISTS (SELECT 1 FROM dbo.ExpenseCodingItem) RETURN;
        END;
        """
    )
    conn.commit()
    try:
        with pytest.raises(pyodbc.Error):
            _run_migration(cur, conn)
        conn.rollback()
        assert cur.execute("SELECT OBJECT_ID('dbo.ExpenseCodingItem', 'U')").fetchval() is not None
    finally:
        conn.rollback()
        cur.execute("IF OBJECT_ID('qbo.U652StrayTrigger', 'TR') IS NOT NULL DROP TRIGGER qbo.U652StrayTrigger;")
        conn.commit()


def test_migration_throws_when_another_schema_has_a_same_named_reference(conn):
    """Only the dbo procedures are dropped, so only they may be exempt."""
    cur = conn.cursor()
    _seed_one_legacy_row(cur, "P-SCH")
    cur.execute("IF SCHEMA_ID('u652audit') IS NULL EXEC('CREATE SCHEMA u652audit');")
    cur.execute(
        """
        CREATE OR ALTER PROCEDURE u652audit.ReadExpenseCodingItemByPublicId AS
        BEGIN
            SET NOCOUNT ON;
            SELECT TOP (1) PublicId FROM dbo.ExpenseCodingItem;
        END;
        """
    )
    conn.commit()
    try:
        with pytest.raises(pyodbc.Error):
            _run_migration(cur, conn)
        conn.rollback()
        assert cur.execute("SELECT OBJECT_ID('dbo.ExpenseCodingItem', 'U')").fetchval() is not None
    finally:
        conn.rollback()
        cur.execute(
            "IF OBJECT_ID('u652audit.ReadExpenseCodingItemByPublicId', 'P') IS NOT NULL "
            "DROP PROCEDURE u652audit.ReadExpenseCodingItemByPublicId;"
        )
        conn.commit()


def test_migration_throws_when_two_old_rows_share_purchase(conn):
    cur = conn.cursor()
    _ensure_eci_table(cur)
    purchase_id, line_id, _ = _insert_expense_purchase(cur, qbo_purchase_qbo_id="P-DUP")
    cur.execute(
        """
        INSERT INTO dbo.ExpenseCodingItem (
            PublicId, QboPurchaseId, QboPurchaseLineId, QboLineId, QboPurchaseQboId, RealmId, Status
        ) VALUES (NEWID(), ?, ?, N'1', N'P-DUP', N'realm-1', N'pending');
        """,
        (purchase_id, line_id),
    )
    cur.execute(
        """
        INSERT INTO qbo.PurchaseLine (QboPurchaseId, QboLineId, LineNum, AccountRefName)
        VALUES (?, N'2', 2, N'Cost of construction : NEED TO CATEGORIZE');
        """,
        (purchase_id,),
    )
    line_id2 = cur.execute("SELECT MAX(Id) FROM qbo.PurchaseLine").fetchval()
    cur.execute(
        """
        INSERT INTO dbo.ExpenseCodingItem (
            PublicId, QboPurchaseId, QboPurchaseLineId, QboLineId, QboPurchaseQboId, RealmId, Status
        ) VALUES (NEWID(), ?, ?, N'2', N'P-DUP', N'realm-1', N'pending');
        """,
        (purchase_id, line_id2),
    )
    conn.commit()
    with pytest.raises(pyodbc.Error):
        _run_migration(cur, conn)
    conn.rollback()
    assert cur.execute("SELECT COUNT(*) FROM dbo.CodingSuggestion").fetchval() == 0
