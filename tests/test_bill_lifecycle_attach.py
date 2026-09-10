"""U-357 Bill — list/GET attach canonical `status` + `review_status_kind`.

Same resolver as Expense. Bill list already stitched Name + flags; this
adds kind + lifecycle status and extends the stitch to single GETs.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.bill.api.router import get_bill_by_id_router
from entities.bill.business.model import Bill
from entities.review.business.model import Review

USER = {"id": 1, "username": "tester"}


def _bill(*, id=1, is_draft=True, vendor_id=10):
    return Bill(
        id=id,
        public_id=f"bill-{id}",
        row_version="AAAA",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=vendor_id,
        payment_term_id=None,
        bill_date="2026-09-01",
        due_date="2026-09-30",
        bill_number=f"B-{id}",
        total_amount=None,
        memo=None,
        is_draft=is_draft,
    )


def _review(*, bill_id, name="Submitted", sort_order=10, is_final=False, is_declined=False):
    return Review(
        id=91,
        public_id="rev-1",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        review_status_id=1,
        user_id=1,
        comments=None,
        bill_id=bill_id,
        expense_id=None,
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


def test_get_by_id_draft_without_review_is_draft_kind_none():
    bill = _bill(id=7, is_draft=True)
    service = MagicMock()
    service.read_by_id.return_value = bill
    review_repo = MagicMock()
    review_repo.read_current_by_bill_id.return_value = None
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch(
             "entities.bill.api.router.ReviewStatusService",
             return_value=SimpleNamespace(
                 get_first_status=lambda: SimpleNamespace(sort_order=10)
             ),
         ), \
         patch("entities.bill.api.router._resolve_vendor_public_id", return_value=None):
        payload = get_bill_by_id_router(id=7, current_user=USER)["data"]
    assert payload["status"] == "draft"
    assert payload["review_status"] is None
    assert payload["review_status_kind"] == "none"


def test_get_by_id_submitted_review_on_draft():
    bill = _bill(id=7, is_draft=True)
    service = MagicMock()
    service.read_by_id.return_value = bill
    review_repo = MagicMock()
    review_repo.read_current_by_bill_id.return_value = _review(bill_id=7)
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch(
             "entities.bill.api.router.ReviewStatusService",
             return_value=SimpleNamespace(
                 get_first_status=lambda: SimpleNamespace(sort_order=10)
             ),
         ), \
         patch("entities.bill.api.router._resolve_vendor_public_id", return_value=None):
        payload = get_bill_by_id_router(id=7, current_user=USER)["data"]
    assert payload["status"] == "submitted"
    assert payload["review_status"] == "Submitted"
    assert payload["review_status_kind"] == "submitted"


def test_get_by_id_finalized_is_completed():
    bill = _bill(id=7, is_draft=False)
    service = MagicMock()
    service.read_by_id.return_value = bill
    review_repo = MagicMock()
    review_repo.read_current_by_bill_id.return_value = None
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch(
             "entities.bill.api.router.ReviewStatusService",
             return_value=SimpleNamespace(
                 get_first_status=lambda: SimpleNamespace(sort_order=10)
             ),
         ), \
         patch("entities.bill.api.router._resolve_vendor_public_id", return_value=None):
        payload = get_bill_by_id_router(id=7, current_user=USER)["data"]
    assert payload["status"] == "completed"
    assert payload["is_draft"] is False
    assert payload["review_status_kind"] == "none"
