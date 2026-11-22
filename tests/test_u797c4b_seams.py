"""U-797c4b (F4, refactored S1) — behavioural tests at the repo / call_procedure seam:
the conn plumbing of the BillCredit line repo, the count, the coding update, and the
exact review-lock sproc name. Fake connection and cursor; the real repository code
runs. No DB."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.bill_credit_line_item.persistence.repo import BillCreditLineItemRepository
from entities.review.persistence.repo import ReviewRepository

REPO = "entities.bill_credit_line_item.persistence.repo"


class FakeCursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.cursors = 0

    def cursor(self):
        self.cursors += 1
        return self._cursor


def _line(**kw):
    base = dict(id=21, row_version_bytes=b"\x00" * 8, bill_credit_id=7, sub_cost_code_id=None,
                project_id=None, description=None, quantity=None, unit_price=None, amount=None,
                is_billable=None, is_billed=None, billable_amount=None, is_draft=None, row_version="rv1")
    base.update(kw)
    return SimpleNamespace(**base)


def test_count_reads_line_count_from_the_locked_sproc_on_the_given_conn():
    conn = FakeConn(FakeCursor(row=SimpleNamespace(LineCount=3)))
    gc = MagicMock()
    with patch(f"{REPO}.get_connection", gc), patch(f"{REPO}.call_procedure") as cp:
        n = BillCreditLineItemRepository().count_by_bill_credit_id_for_update(7, conn=conn)
    assert n == 3
    assert cp.call_args.kwargs["name"] == "CountBillCreditLineItemsByBillCreditIdForUpdate"
    assert cp.call_args.kwargs["params"] == {"BillCreditId": 7}
    assert conn.cursors == 1
    gc.assert_not_called()


def test_update_by_id_runs_the_sproc_on_the_given_conn():
    conn = FakeConn(FakeCursor(row=SimpleNamespace(tag="row")))
    gc = MagicMock()
    with patch(f"{REPO}.get_connection", gc), patch(f"{REPO}.call_procedure") as cp, \
            patch.object(BillCreditLineItemRepository, "_from_db", side_effect=lambda row: row):
        out = BillCreditLineItemRepository().update_by_id(_line(sub_cost_code_id=10), conn=conn)
    assert out.tag == "row"
    assert cp.call_args.kwargs["name"] == "UpdateBillCreditLineItemById"
    assert cp.call_args.kwargs["params"]["SubCostCodeId"] == 10
    assert conn.cursors == 1
    gc.assert_not_called()


class _FakeLineRepo:
    def __init__(self):
        self.updates = []
        self.count_calls = []

    def update_by_id(self, line, *, conn=None):
        self.updates.append((line, conn))
        return line

    def count_by_bill_credit_id_for_update(self, bill_credit_id, *, conn=None):
        self.count_calls.append((bill_credit_id, conn))
        return 2


def test_update_coding_sets_scc_description_and_row_version_and_passes_conn():
    repo = _FakeLineRepo()
    svc = BillCreditLineItemService(repo=repo)
    conn = object()
    line = _line(description="old")
    out = svc.update_coding_in_transaction(
        line_item=line, row_version="rv-new", sub_cost_code_id=55, description="Freight", conn=conn,
    )
    assert out is line
    assert (line.sub_cost_code_id, line.description, line.row_version) == (55, "Freight", "rv-new")
    assert repo.updates == [(line, conn)]


def test_update_coding_keeps_description_when_none_is_given():
    repo = _FakeLineRepo()
    svc = BillCreditLineItemService(repo=repo)
    line = _line(description="kept")
    svc.update_coding_in_transaction(line_item=line, row_version="rv", sub_cost_code_id=3, description=None, conn=object())
    assert line.description == "kept"


def test_service_count_passes_the_conn_through():
    repo = _FakeLineRepo()
    svc = BillCreditLineItemService(repo=repo)
    conn = object()
    assert svc.count_by_bill_credit_id_for_update(7, conn=conn) == 2
    assert repo.count_calls == [(7, conn)]


def test_review_lock_read_calls_the_exact_sproc_name():
    repo = ReviewRepository()
    conn = object()
    with patch.object(ReviewRepository, "_read_current", return_value=None) as rc:
        repo.read_current_by_bill_credit_id_for_update(7, conn=conn)
    rc.assert_called_once()
    assert rc.call_args.args[0] == "ReadCurrentReviewByBillCreditIdForUpdate"
    assert rc.call_args.args[1] == {"BillCreditId": 7}
    assert rc.call_args.kwargs["conn"] is conn
