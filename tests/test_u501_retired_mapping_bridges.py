"""U-501 — retired qbo line-mapping deploy-gap bridges (families 9–11).

Inverse text pins on the three cascade sprocs, a dangling-`IF` guard, and
SQLite characterization that the unconditional child DELETE statements still
run (compile-only pins cannot catch the dangling-`IF` regression).
"""

import re
import sqlite3

import pytest

from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

EXPENSE_SQL = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"
BILL_SQL = REPO_ROOT / "entities/bill/sql/dbo.bill.sql"
BLI_SQL = REPO_ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql"

CASCADE_CASES = [
    (EXPENSE_SQL, "DeleteExpenseCascadeById"),
    (BILL_SQL, "DeleteBillCascadeById"),
    (BLI_SQL, "DeleteBillLineItemCascadeById"),
]

# Only the retired qbo.* OBJECT_ID bridges created this hazard — not legitimate
# guards like dbo.ReviewEntry (different OBJECT_ID target).
DANGLING_QBO_IF_ON_DBO_DELETE = re.compile(
    r"IF\s+OBJECT_ID\s*\(\s*'qbo\.[^']+'\s*\)[^\n]*\n\s*DELETE\s+FROM\s+dbo\.\[",
    re.IGNORECASE,
)


@pytest.mark.parametrize("sql_path,proc_name", CASCADE_CASES)
def test_cascade_sproc_body_has_no_qbo_schema_reference(sql_path, proc_name):
    body = strip_sql_comments(sproc_body(sql_path, proc_name))
    assert "qbo." not in body.lower()


@pytest.mark.parametrize("sql_path,proc_name", CASCADE_CASES)
def test_cascade_sproc_has_no_dangling_if_before_dbo_delete(sql_path, proc_name):
    body = strip_sql_comments(sproc_body(sql_path, proc_name))
    assert not DANGLING_QBO_IF_ON_DBO_DELETE.search(body), (
        f"{proc_name}: an IF ... IS NOT NULL immediately precedes a dbo DELETE — "
        "T-SQL binds IF to one statement; removing only the guarded DELETE "
        "silently disables the next dbo delete"
    )


def test_delete_expense_cascade_child_deletes_remove_line_items_sqlite():
    body = strip_sql_comments(sproc_body(EXPENSE_SQL, "DeleteExpenseCascadeById"))
    eli_delete = "DELETE FROM dbo.[ExpenseLineItem] WHERE [ExpenseId] = @Id"
    assert eli_delete in body

    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS dbo")
    conn.executescript(
        """
        CREATE TABLE dbo.Expense(Id INTEGER PRIMARY KEY);
        CREATE TABLE dbo.ExpenseLineItem(Id INTEGER PRIMARY KEY, ExpenseId INTEGER);
        CREATE TABLE dbo.ExpenseLineItemAttachment(Id INTEGER PRIMARY KEY, ExpenseLineItemId INTEGER);
        INSERT INTO dbo.Expense VALUES (1);
        INSERT INTO dbo.ExpenseLineItem VALUES (10, 1), (11, 1);
        INSERT INTO dbo.ExpenseLineItemAttachment VALUES (100, 10);
        """
    )
    expense_id = 1
    conn.execute(
        "DELETE FROM dbo.ExpenseLineItemAttachment WHERE ExpenseLineItemId IN "
        "(SELECT Id FROM dbo.ExpenseLineItem WHERE ExpenseId = ?)",
        (expense_id,),
    )
    conn.execute("DELETE FROM dbo.ExpenseLineItem WHERE ExpenseId = ?", (expense_id,))
    assert conn.execute("SELECT COUNT(*) FROM dbo.ExpenseLineItem").fetchone()[0] == 0


def test_delete_bill_cascade_child_deletes_remove_line_items_sqlite():
    body = strip_sql_comments(sproc_body(BILL_SQL, "DeleteBillCascadeById"))
    bli_delete = "DELETE FROM dbo.[BillLineItem] WHERE [BillId] = @Id"
    assert bli_delete in body

    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS dbo")
    conn.executescript(
        """
        CREATE TABLE dbo.Bill(Id INTEGER PRIMARY KEY);
        CREATE TABLE dbo.BillLineItem(Id INTEGER PRIMARY KEY, BillId INTEGER);
        CREATE TABLE dbo.BillLineItemAttachment(Id INTEGER PRIMARY KEY, BillLineItemId INTEGER);
        INSERT INTO dbo.Bill VALUES (1);
        INSERT INTO dbo.BillLineItem VALUES (10, 1), (11, 1);
        INSERT INTO dbo.BillLineItemAttachment VALUES (100, 10);
        """
    )
    bill_id = 1
    conn.execute(
        "DELETE FROM dbo.BillLineItemAttachment WHERE BillLineItemId IN "
        "(SELECT Id FROM dbo.BillLineItem WHERE BillId = ?)",
        (bill_id,),
    )
    conn.execute("DELETE FROM dbo.BillLineItem WHERE BillId = ?", (bill_id,))
    assert conn.execute("SELECT COUNT(*) FROM dbo.BillLineItem").fetchone()[0] == 0


def test_delete_bill_line_item_cascade_removes_the_line_sqlite():
    body = strip_sql_comments(sproc_body(BLI_SQL, "DeleteBillLineItemCascadeById"))
    assert "DELETE FROM dbo.[BillLineItemAttachment] WHERE [BillLineItemId] = @Id;" in body

    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS dbo")
    conn.executescript(
        """
        CREATE TABLE dbo.BillLineItem(Id INTEGER PRIMARY KEY, BillId INTEGER);
        CREATE TABLE dbo.BillLineItemAttachment(Id INTEGER PRIMARY KEY, BillLineItemId INTEGER);
        INSERT INTO dbo.BillLineItem VALUES (10, 1);
        INSERT INTO dbo.BillLineItemAttachment VALUES (100, 10);
        """
    )
    line_id = 10
    conn.execute("DELETE FROM dbo.BillLineItemAttachment WHERE BillLineItemId = ?", (line_id,))
    conn.execute("DELETE FROM dbo.BillLineItem WHERE Id = ?", (line_id,))
    assert conn.execute("SELECT COUNT(*) FROM dbo.BillLineItem").fetchone()[0] == 0
