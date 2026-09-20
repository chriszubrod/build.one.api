"""U-486 Phase C1 — expense review-submit notification (pure-logic / no DB)."""

import inspect
import re
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.expense.business.service import ExpenseService, _build_qbo_expense_url
from entities.review.business.notification_service import ReviewNotificationService
from entities.review.business.recipient_model import ResolvedRecipient
from entities.review.business.service import ReviewService

REVIEW_SQL = Path(__file__).resolve().parents[1] / "entities/review/sql/dbo.review.sql"

LIVE_FIRST_STATUS_ID = 1


def _first_status():
    return SimpleNamespace(id=LIVE_FIRST_STATUS_ID, name="Submitted", sort_order=10)


def _submitted_review(*, expense_id=55, review_status_id=LIVE_FIRST_STATUS_ID):
    return SimpleNamespace(
        id=101,
        review_status_id=review_status_id,
        user_id=17,
        expense_id=expense_id,
        bill_id=None,
        contract_labor_id=None,
        status_is_final=False,
        status_is_declined=False,
    )


def _review_service_with_stubbed_create(review):
    svc = ReviewService()
    svc.repo = MagicMock()
    svc.review_status_service = MagicMock()
    svc.review_status_service.get_first_status.return_value = _first_status()
    svc.repo.create.return_value = review
    return svc


# ---------------------------------------------------------------------------
# 1 — initial submit enqueues; subsequent submit does not
# ---------------------------------------------------------------------------


def test_expense_initial_submit_enqueues_notification():
    review = _submitted_review()
    svc = _review_service_with_stubbed_create(review)
    expense = SimpleNamespace(id=55, public_id="exp-pub")

    with patch(
        "entities.expense.persistence.repo.ExpenseRepository.read_by_id",
        return_value=expense,
    ), patch(
        "entities.review.business.notification_service.ReviewNotificationService.enqueue_for_expense"
    ) as mock_enqueue:
        result = svc.create(
            review_status_id=LIVE_FIRST_STATUS_ID,
            user_id=17,
            expense_id=55,
        )

    assert result is review
    mock_enqueue.assert_called_once()
    kwargs = mock_enqueue.call_args.kwargs
    assert kwargs["review"] is review
    assert kwargs["exclude_user_id"] == 17
    assert kwargs["expense"] is expense


def test_expense_non_initial_submit_does_not_enqueue():
    review = _submitted_review(review_status_id=2)
    svc = _review_service_with_stubbed_create(review)

    with patch(
        "entities.review.business.notification_service.ReviewNotificationService.enqueue_for_expense"
    ) as mock_enqueue:
        svc.create(
            review_status_id=2,
            user_id=17,
            expense_id=55,
        )

    mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# 2 — notification failure never propagates
# ---------------------------------------------------------------------------


def test_expense_notification_failure_does_not_propagate():
    review = _submitted_review()
    svc = _review_service_with_stubbed_create(review)

    with patch(
        "entities.review.business.notification_service.ReviewNotificationService.enqueue_for_expense",
        side_effect=RuntimeError("smtp down"),
    ):
        result = svc.create(
            review_status_id=LIVE_FIRST_STATUS_ID,
            user_id=17,
            expense_id=55,
        )

    assert result is review
    svc.repo.create.assert_called_once()


# ---------------------------------------------------------------------------
# 3 — QBO deep link path per PaymentType
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payment_type,expected_segment",
    [
        ("CreditCard", "/app/expense?"),
        ("Cash", "/app/expense?"),
        ("Check", "/app/check?"),
    ],
)
def test_qbo_expense_deep_link_path_by_payment_type(payment_type, expected_segment):
    url = _build_qbo_expense_url(
        qbo_id="123",
        realm_id="realm-1",
        payment_type=payment_type,
    )
    assert expected_segment in url
    assert "txnId=123" in url
    assert "realmId=realm-1" in url
    if payment_type == "Check":
        assert "/app/expense?" not in url


# ---------------------------------------------------------------------------
# 4 — unresolved purchase/realm → None, email still enqueues
# ---------------------------------------------------------------------------


def _expense_enqueue_patch_stack(*, qbo_url=None, recipients=None, attachment=None):
    recipients = recipients if recipients is not None else []
    stack = ExitStack()
    stack.enter_context(patch.object(ExpenseService, "get_qbo_expense_url", return_value=qbo_url))
    stack.enter_context(
        patch.object(ExpenseService, "_build_qbo_url_for_expense", return_value=qbo_url)
    )
    stack.enter_context(
        patch(
            "entities.review.persistence.repo.ReviewRepository.resolve_review_recipients_by_expense_id",
            return_value=recipients,
        )
    )
    stack.enter_context(
        patch(
            "entities.review.business.notification_service.ReviewNotificationService._build_expense_attachment_payload",
            return_value=attachment,
        )
    )
    mock_settings = stack.enter_context(patch("config.Settings"))
    mock_settings.return_value.invoice_inbox_email = "archive@example.com"
    stack.enter_context(
        patch(
            "entities.expense_line_item.business.service.ExpenseLineItemService.read_by_expense_id",
            return_value=[],
        )
    )
    stack.enter_context(
        patch(
            "entities.vendor.business.service.VendorService.read_by_id",
            return_value=SimpleNamespace(name="Acme"),
        )
    )
    stack.enter_context(
        patch(
            "entities.user.business.service.UserService.read_by_id",
            return_value=SimpleNamespace(firstname="Sam", lastname="Sub"),
        )
    )
    mock_outbox_cls = stack.enter_context(
        patch("integrations.ms.outbox.business.service.MsOutboxService")
    )
    stack.enter_context(patch.object(ReviewNotificationService, "_advance_expense_to_in_review"))

    @contextmanager
    def _noop_system_authz():
        from shared.authz.context import set_authz_context

        set_authz_context(
            user_id=None,
            company_id=None,
            is_system_admin=True,
            is_system_context=True,
        )
        try:
            yield
        finally:
            set_authz_context(
                user_id=None,
                company_id=None,
                is_system_admin=False,
                is_system_context=False,
            )

    stack.enter_context(patch("shared.authz.context.system_authz", _noop_system_authz))
    return stack, mock_outbox_cls


def test_deep_link_none_still_enqueues_without_half_built_url():
    expense = SimpleNamespace(
        id=5,
        public_id="exp-pub",
        vendor_id=1,
        reference_number="REF-1",
        total_amount="42.00",
        created_datetime="2026-09-01",
    )
    review = SimpleNamespace(id=9, user_id=17)

    stack, MockOutbox = _expense_enqueue_patch_stack(qbo_url=None, recipients=[])
    with stack:
        mock_outbox = MockOutbox.return_value
        mock_outbox.enqueue_send_mail.return_value = SimpleNamespace(public_id="ob-1")

        ReviewNotificationService().enqueue_for_expense(
            expense=expense,
            review=review,
        )

    body = mock_outbox.enqueue_send_mail.call_args.kwargs["body"]
    assert "app.qbo.intuit.com" not in body
    assert "txnId=" not in body


def test_no_attachment_still_enqueues_and_body_has_no_qbo_link():
    expense = SimpleNamespace(
        id=5,
        public_id="exp-pub",
        vendor_id=1,
        reference_number="REF-1",
        total_amount="42.00",
        created_datetime="2026-09-01",
    )
    review = SimpleNamespace(id=9, user_id=17)
    qbo_url = "https://app.qbo.intuit.com/app/expense?txnId=99&realmId=r1"
    pm = ResolvedRecipient(
        user_id=1,
        firstname="Pat",
        lastname="Lee",
        email="pat@example.com",
        role_name="Project Manager",
        project_id=10,
    )

    stack, MockOutbox = _expense_enqueue_patch_stack(
        qbo_url=qbo_url,
        recipients=[pm],
        attachment=None,
    )
    with stack:
        mock_outbox = MockOutbox.return_value
        mock_outbox.enqueue_send_mail.return_value = SimpleNamespace(public_id="ob-1")

        ReviewNotificationService().enqueue_for_expense(
            expense=expense,
            review=review,
        )

    kwargs = mock_outbox.enqueue_send_mail.call_args.kwargs
    # The valuable half of this spec: 281 of the 316 coding drafts have NO
    # receipt, so the no-attachment path is the COMMON case and must still send.
    assert kwargs["attachment"] is None
    assert kwargs["body"]

    # Chris, 2026-09-20: the "Open in QuickBooks" line was added for exactly
    # those 281, then removed after seeing it in a real draft — he deletes it by
    # hand every time. The body must carry NO QBO link.
    assert "app.qbo.intuit.com" not in kwargs["body"]
    assert "Open in QuickBooks" not in kwargs["body"]


# ---------------------------------------------------------------------------
# 6 — non-PDF attachment skipped (same semantics as Bill)
# ---------------------------------------------------------------------------


def test_non_pdf_expense_attachment_is_skipped():
    expense = SimpleNamespace(public_id="exp-pub")
    line_items = [SimpleNamespace(public_id="eli-pub", id=1)]
    elia = MagicMock()
    elia.read_by_expense_line_item_id.return_value = SimpleNamespace(attachment_id=9)
    attachment_service = MagicMock()
    attachment_service.read_by_id.return_value = SimpleNamespace(
        public_id="att-1",
        blob_url="https://blob/x",
        content_type="image/jpeg",
        filename="receipt.jpg",
    )
    storage = MagicMock()

    payload = ReviewNotificationService._build_expense_attachment_payload(
        expense=expense,
        line_items=line_items,
        elia_service=elia,
        attachment_service=attachment_service,
        storage=storage,
    )

    assert payload is None
    storage.download_file.assert_not_called()


# ---------------------------------------------------------------------------
# 7 — SQL source pins
# ---------------------------------------------------------------------------


def _sproc_names_in_review_sql():
    text = REVIEW_SQL.read_text(encoding="utf-8")
    return set(re.findall(r"CREATE OR ALTER PROCEDURE dbo\.(\w+)", text))


def test_resolve_review_recipients_by_expense_id_sproc_source_pins():
    text = REVIEW_SQL.read_text(encoding="utf-8")
    match = re.search(
        r"CREATE OR ALTER PROCEDURE dbo\.ResolveReviewRecipientsByExpenseId\b(.*?)^GO",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "ResolveReviewRecipientsByExpenseId sproc missing"
    body = match.group(1)
    assert body.lstrip().startswith("\n(") or body.lstrip().startswith("(")
    assert "SET NOCOUNT ON" in body
    assert "ExpenseLineItem" in body
    assert "Expense" in body
    assert "'Project Manager'" in body and "'Owner'" in body


def test_review_sql_defines_prior_sprocs_plus_exactly_one_new_resolver():
    names = _sproc_names_in_review_sql()
    assert "ResolveReviewRecipientsByBillId" in names
    assert "ResolveReviewRecipientsByContractLaborId" in names
    assert "ResolveReviewRecipientsByExpenseId" in names
    resolver_names = [n for n in names if n.startswith("ResolveReviewRecipientsBy")]
    assert len(resolver_names) == 3


def test_enqueue_for_expense_swallows_exceptions():
    src = inspect.getsource(ReviewNotificationService.enqueue_for_expense)
    assert "logger.exception" in src
    assert "except Exception" in src
