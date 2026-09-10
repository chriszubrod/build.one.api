"""U-357 Expense-first — list/GET attach `status` + `review_status*` (LS-01a slice).

Zero schema change: Review state is stitched from dbo.Review the same way
Bill list already stitches ReadCurrentReviewsByBillIds. Additive fields
only; is_draft is preserved.
"""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from entities.expense.api.router import (
    get_expense_by_public_id_router,
    get_expense_by_reference_number_and_vendor_router,
    get_expenses_router,
)
from entities.expense.business.model import Expense
from entities.expense.business.service import ExpenseService
from entities.review.business.model import Review

USER = {"id": 1, "username": "tester"}
REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SQL = REPO_ROOT / "entities" / "review" / "sql" / "dbo.review.sql"

LIFECYCLE_KEYS = {
    "status",
    "review_status",
    "review_status_kind",
    "review_status_is_final",
    "review_status_is_declined",
}


def _expense(*, id=1, is_draft=True, public_id=None):
    return Expense(
        id=id,
        public_id=public_id or f"exp-{id}",
        row_version="AAAA",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=10,
        expense_date="2026-09-01",
        reference_number=f"R-{id}",
        total_amount=Decimal("12.00"),
        memo=None,
        is_draft=is_draft,
        is_credit=False,
    )


def _review(
    *,
    expense_id,
    name="Submitted",
    sort_order=10,
    is_final=False,
    is_declined=False,
):
    return Review(
        id=91,
        public_id="rev-1",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        review_status_id=1,
        user_id=1,
        comments=None,
        bill_id=None,
        expense_id=expense_id,
        bill_credit_id=None,
        invoice_id=None,
        status_name=name,
        status_sort_order=sort_order,
        status_is_final=is_final,
        status_is_declined=is_declined,
        status_color=None,
        user_firstname=None,
        user_lastname=None,
    )


def _patch_router(service, review_repo, first_sort_order=10):
    status_svc = MagicMock()
    status_svc.get_first_status.return_value = SimpleNamespace(sort_order=first_sort_order)
    return (
        patch("entities.expense.api.router.ExpenseService", return_value=service),
        patch("entities.expense.api.router.ReviewRepository", return_value=review_repo),
        patch("entities.expense.api.router.ReviewStatusService", return_value=status_svc),
    )


def _call_list(expenses, review_map=None):
    service = MagicMock()
    service.read_paginated.return_value = expenses
    service.count.return_value = len(expenses)
    review_repo = MagicMock()
    review_repo.read_current_by_expense_ids.return_value = review_map or {}
    p_svc, p_rev, p_status = _patch_router(service, review_repo)
    with p_svc, p_rev, p_status:
        return get_expenses_router(
            page=1,
            page_size=50,
            search=None,
            vendor_id=None,
            is_draft=None,
            current_user=USER,
        ), review_repo


def _call_get(expense, review=None):
    service = MagicMock()
    service.read_by_public_id.return_value = expense
    review_repo = MagicMock()
    review_repo.read_current_by_expense_id.return_value = review
    p_svc, p_rev, p_status = _patch_router(service, review_repo)
    with p_svc, p_rev, p_status:
        return get_expense_by_public_id_router(
            public_id=expense.public_id, current_user=USER
        )


def _call_get_by_reference(expense, review=None):
    service = MagicMock()
    service.read_by_reference_number_and_vendor_public_id.return_value = expense
    review_repo = MagicMock()
    review_repo.read_current_by_expense_id.return_value = review
    p_svc, p_rev, p_status = _patch_router(service, review_repo)
    with p_svc, p_rev, p_status:
        return get_expense_by_reference_number_and_vendor_router(
            reference_number=expense.reference_number,
            vendor_public_id="vendor-pub",
            current_user=USER,
        )


# --------------------------------------------------------------------------
# GET /get/expenses
# --------------------------------------------------------------------------


def test_list_qbo_pulled_expense_is_completed_with_kind_none():
    """Prod majority: IsDraft=0, no Review row → status=completed, kind=none."""
    response, _ = _call_list([_expense(id=1, is_draft=False)])
    row = response["data"][0]
    assert row["status"] == "completed"
    assert row["review_status"] is None
    assert row["review_status_kind"] == "none"
    assert row["is_draft"] is False


def test_list_draft_without_review_is_draft_kind_none():
    response, _ = _call_list([_expense(id=1, is_draft=True)])
    row = response["data"][0]
    assert row["status"] == "draft"
    assert row["review_status_kind"] == "none"


def test_list_maps_each_expense_to_its_own_review():
    submitted = _review(expense_id=1, name="Submitted", sort_order=10)
    approved = _review(
        expense_id=2, name="Approved", sort_order=30, is_final=True
    )
    response, review_repo = _call_list(
        [_expense(id=1, is_draft=True), _expense(id=2, is_draft=True), _expense(id=3, is_draft=False)],
        review_map={1: submitted, 2: approved},
    )
    by_id = {row["id"]: row for row in response["data"]}
    assert by_id[1]["status"] == "submitted"
    assert by_id[1]["review_status"] == "Submitted"
    assert by_id[1]["review_status_kind"] == "submitted"
    assert by_id[2]["status"] == "approved"
    assert by_id[2]["review_status_kind"] == "approved"
    assert by_id[3]["status"] == "completed"
    assert by_id[3]["review_status_kind"] == "none"
    review_repo.read_current_by_expense_ids.assert_called_once_with([1, 2, 3])


def test_list_empty_skips_the_batch_read():
    response, review_repo = _call_list([])
    assert response["data"] == []
    review_repo.read_current_by_expense_ids.assert_not_called()


def test_list_degrades_when_batch_review_read_fails():
    """New sproc may not be live yet — list must still return expenses."""
    service = MagicMock()
    expenses = [_expense(id=1, is_draft=False)]
    service.read_paginated.return_value = expenses
    service.count.return_value = 1
    review_repo = MagicMock()
    review_repo.read_current_by_expense_ids.side_effect = RuntimeError("sproc missing")
    p_svc, p_rev, p_status = _patch_router(service, review_repo)
    with p_svc, p_rev, p_status:
        response = get_expenses_router(
            page=1,
            page_size=50,
            search=None,
            vendor_id=None,
            is_draft=None,
            current_user=USER,
        )
    row = response["data"][0]
    assert row["id"] == 1
    assert row["status"] == "completed"
    assert row["review_status_kind"] == "none"


def test_list_preserves_every_pre_existing_field():
    expense = _expense(id=1, is_draft=False)
    original = expense.to_dict()
    response, _ = _call_list([expense])
    row = response["data"][0]
    for key, value in original.items():
        assert row[key] == value, f"pre-existing field {key!r} changed"
    assert set(row) - set(original) == LIFECYCLE_KEYS
    assert set(response) == {"data", "count", "page", "page_size"}


# --------------------------------------------------------------------------
# GET /get/expense/{public_id}  and  by-reference
# --------------------------------------------------------------------------


@pytest.mark.parametrize("call", [_call_get, _call_get_by_reference])
def test_single_reads_attach_submitted_review(call):
    expense = _expense(id=7, is_draft=True)
    payload = call(expense, review=_review(expense_id=7))["data"]
    assert payload["status"] == "submitted"
    assert payload["review_status"] == "Submitted"
    assert payload["review_status_kind"] == "submitted"
    assert payload["review_status_is_final"] is False
    assert payload["review_status_is_declined"] is False


@pytest.mark.parametrize("call", [_call_get, _call_get_by_reference])
def test_single_reads_preserve_every_pre_existing_field(call):
    expense = _expense(id=7, is_draft=False)
    original = expense.to_dict()
    payload = call(expense)["data"]
    for key, value in original.items():
        assert payload[key] == value, f"pre-existing field {key!r} changed"
    assert LIFECYCLE_KEYS <= set(payload)


def test_single_read_degrades_when_current_review_fails():
    expense = _expense(id=7, is_draft=True)
    service = MagicMock()
    service.read_by_public_id.return_value = expense
    review_repo = MagicMock()
    review_repo.read_current_by_expense_id.side_effect = RuntimeError("db")
    p_svc, p_rev, p_status = _patch_router(service, review_repo)
    with p_svc, p_rev, p_status:
        payload = get_expense_by_public_id_router(
            public_id=expense.public_id, current_user=USER
        )["data"]
    assert payload["status"] == "draft"
    assert payload["review_status_kind"] == "none"


# --------------------------------------------------------------------------
# Delete cascade
# --------------------------------------------------------------------------


def test_delete_clears_reviews_before_header():
    expense = SimpleNamespace(id=99, public_id="exp-pub")
    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = expense
    svc = ExpenseService(repo=mock_repo)
    review_repo = MagicMock()
    with patch.object(svc, "read_by_public_id", return_value=expense), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as li_svc_cls, patch(
        "entities.review.persistence.repo.ReviewRepository", return_value=review_repo
    ):
        li_svc_cls.return_value.read_by_expense_id.return_value = []
        result = svc.delete_by_public_id("exp-pub")

    assert result is expense
    review_repo.delete_by_expense_id.assert_called_once_with(99)
    mock_repo.delete_by_id.assert_called_once_with(99)


def test_delete_aborts_when_review_cascade_fails():
    expense = SimpleNamespace(id=99, public_id="exp-pub")
    mock_repo = Mock()
    svc = ExpenseService(repo=mock_repo)
    review_repo = MagicMock()
    review_repo.delete_by_expense_id.side_effect = RuntimeError("547")
    review_repo.read_by_expense_id.return_value = [_review(expense_id=99)]
    with patch.object(svc, "read_by_public_id", return_value=expense), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as li_svc_cls, patch(
        "entities.review.persistence.repo.ReviewRepository", return_value=review_repo
    ):
        li_svc_cls.return_value.read_by_expense_id.return_value = []
        with pytest.raises(ValueError, match="Review"):
            svc.delete_by_public_id("exp-pub")

    mock_repo.delete_by_id.assert_not_called()


def test_delete_continues_when_cascade_sproc_missing_and_no_reviews():
    """SQL apply owed: unreviewed expenses must still delete."""
    expense = SimpleNamespace(id=99, public_id="exp-pub")
    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = expense
    svc = ExpenseService(repo=mock_repo)
    review_repo = MagicMock()
    review_repo.delete_by_expense_id.side_effect = RuntimeError("sproc missing")
    review_repo.read_by_expense_id.return_value = []
    with patch.object(svc, "read_by_public_id", return_value=expense), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as li_svc_cls, patch(
        "entities.review.persistence.repo.ReviewRepository", return_value=review_repo
    ):
        li_svc_cls.return_value.read_by_expense_id.return_value = []
        result = svc.delete_by_public_id("exp-pub")

    assert result is expense
    mock_repo.delete_by_id.assert_called_once_with(99)


def test_expense_review_sprocs_live_in_review_base():
    text = REVIEW_SQL.read_text(encoding="utf-8")
    assert "CREATE OR ALTER PROCEDURE ReadCurrentReviewsByExpenseIds" in text
    assert "CREATE OR ALTER PROCEDURE DeleteReviewsByExpenseId" in text
    assert "PARTITION BY r.[ExpenseId]" in text
