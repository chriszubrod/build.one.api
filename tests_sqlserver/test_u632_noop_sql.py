"""U-632 F11: the HOLDING_CODE gate must not break the sprocs' NO-OP contracts.

FinalizeBy*Id on an already-completed document re-SELECTs the row (idempotent);
Transition*Status returns the row when Status == @ToStatus and NOTHING when
Status is outside @FromStatuses. A holding line on the document must not turn
either no-op into a HOLDING_CODE raise; a real move into approved still must.
Run this file ALONE (it drops/recreates the shared fixture tables)."""
from __future__ import annotations

import pyodbc
import pytest

from tests_sqlserver.test_u632_holding_code_sql import (  # noqa: F401  (conn is a fixture)
    _bill_id,
    _exec_expect_holding_refusal,
    _expense_id,
    _insert_scc,
    conn,
    pytestmark,
)

_DOCS = {
    "Bill": ("dbo.Bill", "dbo.BillLineItem", "BillId", _bill_id),
    "Expense": ("dbo.Expense", "dbo.ExpenseLineItem", "ExpenseId", _expense_id),
}


def _doc_with_holding_line(cur, kind: str, status: str) -> int:
    _insert_scc(cur, 1, "00.02", holding=True)
    _, line_tbl, fk, make = _DOCS[kind]
    doc_id = make(cur, status=status)
    cur.execute(f"INSERT INTO {line_tbl} ({fk}, SubCostCodeId) VALUES (?, ?)", doc_id, 1)
    return doc_id


def _status(cur, kind: str, doc_id: int) -> str:
    cur.execute(f"SELECT Status FROM {_DOCS[kind][0]} WHERE Id=?", doc_id)
    return cur.fetchone()[0]


def _exec_rows(cur, sql: str, *params):
    cur.execute(sql, params)
    return cur.fetchall() if cur.description else []


@pytest.mark.parametrize("kind", ["Bill", "Expense"])
def test_f11_finalize_already_completed_with_holding_line_is_noop(conn, kind):
    cur = conn.cursor()
    doc_id = _doc_with_holding_line(cur, kind, "completed")
    rows = _exec_rows(cur, f"EXEC dbo.Finalize{kind}ById @Id=?", doc_id)
    assert len(rows) == 1
    assert int(rows[0].Id) == doc_id
    assert rows[0].Status == "completed"
    assert _status(cur, kind, doc_id) == "completed"


@pytest.mark.parametrize("kind", ["Bill", "Expense"])
@pytest.mark.parametrize("status", ["approved", "completed"])
def test_f11_transition_to_current_status_with_holding_line_returns_row(conn, kind, status):
    cur = conn.cursor()
    doc_id = _doc_with_holding_line(cur, kind, status)
    rows = _exec_rows(
        cur,
        f"EXEC dbo.Transition{kind}Status @Id=?, @FromStatuses=?, @ToStatus=?",
        doc_id,
        f"submitted,in_review,{status}",
        status,
    )
    assert len(rows) == 1 and rows[0].Status == status
    assert _status(cur, kind, doc_id) == status


@pytest.mark.parametrize("kind", ["Bill", "Expense"])
def test_f11_transition_from_status_mismatch_with_holding_line_returns_no_row(conn, kind):
    cur = conn.cursor()
    doc_id = _doc_with_holding_line(cur, kind, "draft")
    rows = _exec_rows(
        cur,
        f"EXEC dbo.Transition{kind}Status @Id=?, @FromStatuses=?, @ToStatus=?",
        doc_id,
        "submitted,in_review",
        "approved",
    )
    assert rows == []
    assert _status(cur, kind, doc_id) == "draft"


@pytest.mark.parametrize("kind", ["Bill", "Expense"])
def test_f11_real_draft_to_approved_with_holding_line_still_refused(conn, kind):
    cur = conn.cursor()
    doc_id = _doc_with_holding_line(cur, kind, "draft")
    _exec_expect_holding_refusal(
        cur,
        f"EXEC dbo.Transition{kind}Status @Id=?, @FromStatuses=?, @ToStatus=?",
        doc_id,
        "draft",
        "approved",
    )
    assert _status(cur, kind, doc_id) == "draft"
