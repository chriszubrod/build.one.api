"""U-652a: UpsertCodingSuggestionForPurchase idempotency with ExpenseId (real SQL Server)."""
from __future__ import annotations

import os
import re
import uuid

import pyodbc
import pytest

from tests_sqlserver import test_u652_coding_suggestion_sql as u652_sql

pytestmark = u652_sql.pytestmark

_LOCAL_SERVERS = u652_sql._LOCAL_SERVERS


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


@pytest.fixture(scope="module")
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = u652_sql._sqlserver_connect(conn_str, autocommit=False)
    name = c.cursor().execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    rcsi = c.cursor().execute(
        "SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = DB_NAME()"
    ).fetchval()
    assert rcsi == 1, "buildone_test must run READ_COMMITTED_SNAPSHOT ON"
    cur = c.cursor()
    u652_sql._drop_coding_artifacts(cur)
    u652_sql._install_supporting_schema(cur)
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _reset(conn):
    conn.rollback()
    cur = conn.cursor()
    cur.execute("DELETE FROM dbo.ExpenseLineItem")
    cur.execute("DELETE FROM dbo.CodingSuggestion")
    cur.execute(
        "IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NOT NULL DROP TABLE dbo.ExpenseCodingItem"
    )
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


def test_double_upsert_same_purchase_preserves_confirmed_status_and_expense_id(conn):
    """Concurrent/repeated submit must not duplicate rows or reset confirmation."""
    cur = conn.cursor()
    purchase_id, _, expense_id = u652_sql._insert_expense_purchase(cur)
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (
            PublicId, TargetKind, QboPurchaseId, QboPurchaseQboId, RealmId, ExpenseId,
            Status, ConfirmedProjectId, ConfirmedSubCostCodeId
        ) VALUES (?, N'expense', ?, N'P-100', N'realm-1', ?, N'confirmed', 5, 6);
        """,
        (pub, purchase_id, expense_id),
    )
    conn.commit()

    for _ in range(2):
        cur.execute(
            """
            EXEC dbo.UpsertCodingSuggestionForPurchase
                @QboPurchaseId=?,
                @QboPurchaseQboId=N'P-100',
                @RealmId=N'realm-1',
                @VendorId=99,
                @ExpenseId=?,
                @CreatedByUserId=17;
            """,
            (purchase_id, expense_id),
        )
    row = cur.execute(
        """
        SELECT Status, ExpenseId, ConfirmedProjectId
        FROM dbo.CodingSuggestion
        WHERE PublicId=?;
        """,
        (pub,),
    ).fetchone()
    assert row.Status == "confirmed"
    assert row.ExpenseId == expense_id
    assert row.ConfirmedProjectId == 5
    assert cur.execute("SELECT COUNT(*) FROM dbo.CodingSuggestion").fetchval() == 1
    conn.commit()
