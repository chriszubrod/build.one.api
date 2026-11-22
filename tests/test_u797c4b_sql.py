"""U-797c4b item 2 — static checks on the new BillCredit locking sprocs.

The new sprocs must be faithful copies of their Bill counterparts: after renaming
BillCredit -> Bill (and BillCreditLineItem -> BillLineItem) the bodies are equal,
so lock hints and lock order cannot drift. Also pins NOCOUNT, GO termination, no
ROLLBACK, and the ROWVERSION check on the update path.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEW_SQL = (ROOT / "entities/review/sql/dbo.review.sql").read_text()
LINE_SQL = (ROOT / "entities/bill_credit_line_item/sql/dbo.bill_credit_line_item.sql").read_text()
BILL_LINE_SQL = (ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql").read_text()


def _body(sql: str, name: str) -> str:
    """The text of one CREATE OR ALTER PROCEDURE, up to its GO terminator."""
    start = sql.index(f"CREATE OR ALTER PROCEDURE {name}\n")
    end = sql.index("\nGO", start)
    return sql[start:end]


def _normalize(text: str, *, line: bool = False) -> str:
    """Drop the sproc name and comments, collapse whitespace, rename BillCredit -> Bill."""
    text = re.sub(r"CREATE OR ALTER PROCEDURE \w+", "", text)
    text = re.sub(r"--[^\n]*", "", text)
    text = text.replace("BillCreditLineItem", "BillLineItem") if line else text
    text = text.replace("BillCreditId", "BillId").replace("BillCredit", "Bill")
    text = text.replace("ByBillCreditIdForUpdate", "ByBillIdForUpdate")
    return re.sub(r"\s+", " ", text).strip()


def test_review_lock_read_is_a_faithful_copy_of_the_bill_one():
    bill = _normalize(_body(REVIEW_SQL, "ReadCurrentReviewByBillIdForUpdate"))
    credit = _normalize(_body(REVIEW_SQL, "ReadCurrentReviewByBillCreditIdForUpdate"))
    assert bill == credit


def test_line_count_is_a_faithful_copy_of_the_bill_one():
    bill = _normalize(_body(BILL_LINE_SQL, "CountBillLineItemsByBillIdForUpdate"), line=True)
    credit = _normalize(_body(LINE_SQL, "CountBillCreditLineItemsByBillCreditIdForUpdate"), line=True)
    assert bill == credit


def test_new_sprocs_have_nocount_and_no_rollback_and_are_go_terminated():
    for sql, name in [
        (REVIEW_SQL, "ReadCurrentReviewByBillCreditIdForUpdate"),
        (LINE_SQL, "CountBillCreditLineItemsByBillCreditIdForUpdate"),
    ]:
        body = _body(sql, name)
        assert "SET NOCOUNT ON" in body, name
        assert "ROLLBACK" not in body.upper(), name
        assert "\nGO" in sql[sql.index(body) + len(body):][:10] or sql[sql.index(body) + len(body):].lstrip().startswith("GO"), name


def test_review_lock_takes_the_parent_lock_before_the_review_lock():
    body = _body(REVIEW_SQL, "ReadCurrentReviewByBillCreditIdForUpdate")
    parent = body.index("dbo.[BillCredit] WITH (UPDLOCK, HOLDLOCK)")
    review = body.index("dbo.[Review] r WITH (UPDLOCK, HOLDLOCK)")
    assert parent < review


def test_update_path_checks_rowversion():
    body = _body(LINE_SQL, "UpdateBillCreditLineItemById")
    assert "[RowVersion] = @RowVersion" in body
    assert "ROLLBACK" not in body.upper()
