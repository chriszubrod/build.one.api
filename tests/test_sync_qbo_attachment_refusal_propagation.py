"""Attachment-sync budget/write-refusal must propagate at pull + backfill call sites (U-211/U-218e)."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.purchase.business.model import QboPurchase
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit
from scripts.backfill_qbo_bills import CREATABLE, apply_backfill
from scripts.sync_qbo_purchase import sync_qbo_to_local as sync_purchase_to_local
from scripts.sync_qbo_vendorcredit import sync_qbo_to_local as sync_vendorcredit_to_local

REALM_ID = "realm-test"


def _budget_error():
    return QboBudgetExceededError(
        "budget exhausted",
        month_key="2026-08",
        call_count=475_001,
        budget=500_000,
    )


def _write_refused_error():
    return QboWriteRefusedError("writes disabled")


def _make_qbo_purchase(*, purchase_id=42, qbo_id="qbo-purchase-123"):
    return QboPurchase(
        id=purchase_id,
        public_id="11111111-1111-1111-1111-111111111111",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        payment_type="CreditCard",
        account_ref_value="1",
        account_ref_name="Card",
        entity_ref_value="1",
        entity_ref_name="Vendor",
        credit=False,
        txn_date="2026-08-01",
        doc_number="EXP-1",
        private_note=None,
        total_amt=Decimal("100.00"),
        currency_ref_value=None,
        currency_ref_name=None,
        exchange_rate=None,
        department_ref_value=None,
        department_ref_name=None,
        global_tax_calculation=None,
    )


def _make_qbo_vendor_credit(*, vendor_credit_id=55, qbo_id="qbo-vc-456"):
    return QboVendorCredit(
        id=vendor_credit_id,
        public_id="22222222-2222-2222-2222-222222222222",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        realm_id=REALM_ID,
        qbo_id=qbo_id,
        sync_token="0",
        vendor_ref_value="1",
        vendor_ref_name="Vendor",
        txn_date="2026-08-01",
        doc_number="VC-1",
        total_amt=Decimal("50.00"),
        private_note=None,
        ap_account_ref_value=None,
        ap_account_ref_name=None,
        currency_ref_value=None,
        currency_ref_name=None,
    )


def _run_purchase_sync_with_attachment_error(error):
    purchase = _make_qbo_purchase()
    outcome = SyncOutcome.for_service_pull()
    outcome.synced = [purchase]

    qbo_purchase_service = MagicMock()
    qbo_purchase_service.sync_from_qbo.return_value = outcome

    expense = SimpleNamespace(id=99, public_id="33333333-3333-3333-3333-333333333333")
    purchase_connector = MagicMock()
    purchase_connector.sync_from_qbo_purchase.return_value = expense

    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_purchase.side_effect = error

    with patch("scripts.sync_qbo_purchase.QboAttachableService", return_value=attachable_service), patch(
        "entities.expense.business.service.ExpenseService"
    ) as mock_expense_svc_cls, patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as mock_eli_svc_cls, patch(
        "scripts.sync_qbo_purchase.read_lines_riding_out_race", return_value=[MagicMock()]
    ), patch(
        "scripts.sync_qbo_purchase.with_retry",
        side_effect=lambda fn, *args, **kwargs: fn(*args),
    ), patch(
        "scripts.sync_qbo_purchase.sync_purchase_attachments_to_expense_line_items"
    ):
        mock_expense_svc_cls.return_value = MagicMock()
        mock_eli_svc_cls.return_value = MagicMock()

        result, returned_outcome = sync_purchase_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_purchase_service=qbo_purchase_service,
            purchase_connector=purchase_connector,
        )

    return result, returned_outcome, purchase


def _run_vendorcredit_sync_with_attachment_error(error):
    vendor_credit = _make_qbo_vendor_credit()
    outcome = SyncOutcome.for_service_pull()
    outcome.synced = [vendor_credit]

    qbo_vendor_credit_service = MagicMock()
    qbo_vendor_credit_service.sync_from_qbo.return_value = outcome

    bill_credit = SimpleNamespace(id=88, public_id="44444444-4444-4444-4444-444444444444")
    vendor_credit_connector = MagicMock()
    vendor_credit_connector.sync_from_qbo_vendor_credit.return_value = bill_credit

    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_vendor_credit.side_effect = error

    with patch(
        "scripts.sync_qbo_vendorcredit.BillCreditCompleteService"
    ) as mock_complete_cls, patch(
        "scripts.sync_qbo_vendorcredit.BillCreditLineItemService"
    ) as mock_bcli_svc_cls, patch(
        "scripts.sync_qbo_vendorcredit.read_lines_riding_out_race", return_value=[MagicMock()]
    ), patch(
        "scripts.sync_qbo_vendorcredit.with_retry",
        side_effect=lambda fn, *args, **kwargs: fn(*args),
    ), patch(
        "scripts.sync_qbo_vendorcredit._link_attachments_to_bill_credit_line_items"
    ):
        mock_complete_cls.return_value = MagicMock()
        mock_bcli_svc_cls.return_value = MagicMock()

        result, returned_outcome = sync_vendorcredit_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_vendor_credit_service=qbo_vendor_credit_service,
            vendor_credit_connector=vendor_credit_connector,
            sync_attachments=True,
            attachable_service=attachable_service,
        )

    return result, returned_outcome, vendor_credit


@pytest.mark.parametrize(
    "error_factory",
    [_budget_error, _write_refused_error],
    ids=["budget_exceeded", "write_refused"],
)
def test_purchase_attachment_refusal_holds_watermark(error_factory):
    error = error_factory()
    result, outcome, purchase = _run_purchase_sync_with_attachment_error(error)
    assert outcome.projected_count == 1
    assert str(purchase.id) in outcome.projection_failed_ids
    assert outcome.should_hold is True
    assert result["expenses_module_synced"] == 1


@pytest.mark.parametrize(
    "error_factory",
    [_budget_error, _write_refused_error],
    ids=["budget_exceeded", "write_refused"],
)
def test_vendorcredit_attachment_refusal_holds_watermark(error_factory):
    error = error_factory()
    result, outcome, vendor_credit = _run_vendorcredit_sync_with_attachment_error(error)
    assert outcome.projected_count == 1
    assert str(vendor_credit.id) in outcome.projection_failed_ids
    assert outcome.should_hold is True
    assert result["vendor_credits_synced"] == 1


@pytest.mark.parametrize(
    "error_factory",
    [_budget_error, _write_refused_error],
    ids=["budget_exceeded", "write_refused"],
)
def test_backfill_attachment_refusal_marks_bill_failed(capsys, error_factory):
    error = error_factory()
    row = {"QboId": "99999", "Id": 42, "bucket": CREATABLE}
    qbo_bill = SimpleNamespace(id=42, qbo_id="99999", total_amt=Decimal("100.00"))

    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_bill.side_effect = error
    bill_module = SimpleNamespace(id=77, public_id="55555555-5555-5555-5555-555555555555")

    with patch(
        "integrations.intuit.qbo.bill.business.service.QboBillService"
    ) as mock_qbo_bill_svc_cls, patch(
        "integrations.intuit.qbo.bill.persistence.repo.QboBillRepository"
    ) as mock_qbo_bill_repo_cls, patch(
        "integrations.intuit.qbo.bill.connector.bill.business.service.BillBillConnector"
    ) as mock_bill_connector_cls, patch(
        "integrations.intuit.qbo.attachable.business.service.QboAttachableService",
        return_value=attachable_service,
    ), patch(
        "integrations.intuit.qbo.auth.business.service.QboAuthService"
    ) as mock_auth_svc_cls, patch(
        "entities.bill.business.service.BillService"
    ) as mock_bill_svc_cls, patch(
        "entities.bill_line_item.business.service.BillLineItemService"
    ) as mock_bli_svc_cls, patch(
        "scripts.sync_qbo_bill._link_attachments_to_bill_line_items"
    ), patch(
        "scripts.backfill_qbo_bills.read_lines_riding_out_race", return_value=[MagicMock()]
    ), patch(
        "scripts.backfill_qbo_bills.with_retry",
        side_effect=lambda fn, *args, **kwargs: fn(*args),
    ):
        mock_qbo_bill_svc_cls.return_value = MagicMock()
        mock_qbo_bill_repo_cls.return_value.read_by_id.return_value = qbo_bill
        mock_bill_connector_cls.return_value.sync_from_qbo_bill.return_value = bill_module
        mock_auth_svc_cls.return_value.read_all.return_value = [
            SimpleNamespace(realm_id=REALM_ID)
        ]
        mock_bill_svc_cls.return_value = MagicMock()
        mock_bli_svc_cls.return_value.read_by_bill_id.return_value = []

        apply_backfill([row], limit=None, include_null=False)

    captured = capsys.readouterr().out
    assert "CREATED qbo_id=99999" in captured
    assert "failed:                1 ['99999']" in captured
