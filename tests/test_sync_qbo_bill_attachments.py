"""Pull-side attachment sync must not swallow budget/write-refusal errors (U-211/U-218e)."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.attachable.business.model import QboAttachable
from integrations.intuit.qbo.attachable.business.service import QboAttachableService
from integrations.intuit.qbo.attachable.connector.attachment.business.service import (
    AttachableAttachmentConnector,
)
from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.bill.business.model import QboBill
from scripts.sync_qbo_bill import sync_qbo_to_local

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


def _make_qbo_attachable(*, attachable_id=1, qbo_id="att-123"):
    return QboAttachable(
        id=attachable_id,
        public_id="33333333-3333-3333-3333-333333333333",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        file_name="invoice.pdf",
        note=None,
        category=None,
        content_type="application/pdf",
        size=1024,
        file_access_uri=None,
        temp_download_uri=None,
        entity_ref_type="Bill",
        entity_ref_value="qbo-123",
    )


def _make_qbo_bill(*, bill_id=42, qbo_id="qbo-123"):
    return QboBill(
        id=bill_id,
        public_id="11111111-1111-1111-1111-111111111111",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        vendor_ref_value="1",
        vendor_ref_name="Vendor",
        txn_date="2026-08-01",
        due_date=None,
        doc_number="INV-1",
        private_note=None,
        total_amt=Decimal("0"),
        balance=None,
        ap_account_ref_value=None,
        ap_account_ref_name=None,
        sales_term_ref_value=None,
        sales_term_ref_name=None,
        currency_ref_value=None,
        currency_ref_name=None,
        exchange_rate=None,
        department_ref_value=None,
        department_ref_name=None,
        global_tax_calculation=None,
    )


def _run_sync_with_attachment_error(error):
    bill = _make_qbo_bill()
    outcome = SyncOutcome.for_service_pull()
    outcome.synced = [bill]

    qbo_bill_service = MagicMock()
    qbo_bill_service.sync_from_qbo.return_value = outcome

    bill_module = SimpleNamespace(
        id=99, public_id="22222222-2222-2222-2222-222222222222"
    )
    bill_connector = MagicMock()
    bill_connector.sync_from_qbo_bill.return_value = bill_module

    attachable_service = MagicMock()
    attachable_service.sync_attachables_for_bill.side_effect = error

    with patch("scripts.sync_qbo_bill.BillService") as mock_bill_svc_cls, patch(
        "scripts.sync_qbo_bill.BillLineItemService"
    ) as mock_bli_svc_cls, patch(
        "scripts.sync_qbo_bill.read_lines_riding_out_race", return_value=[]
    ), patch(
        "scripts.sync_qbo_bill.with_retry",
        side_effect=lambda fn, *args, **kwargs: fn(*args),
    ), patch(
        "scripts.sync_qbo_bill._link_attachments_to_bill_line_items"
    ):
        mock_bill_svc_cls.return_value = MagicMock()
        mock_bli_svc_cls.return_value = MagicMock()

        result, returned_outcome = sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_bill_service=qbo_bill_service,
            bill_connector=bill_connector,
            sync_attachments=True,
            attachable_service=attachable_service,
        )

    return result, returned_outcome, bill


def test_attachment_sync_budget_exceeded_holds_watermark():
    error = _budget_error()
    result, outcome, bill = _run_sync_with_attachment_error(error)
    assert outcome.projected_count == 1
    assert str(bill.qbo_id) in outcome.projection_failed_ids
    assert outcome.should_hold is True
    assert result["bills_module_synced"] == 1


def test_attachment_sync_write_refused_holds_watermark():
    error = _write_refused_error()
    result, outcome, bill = _run_sync_with_attachment_error(error)
    assert outcome.projected_count == 1
    assert str(bill.qbo_id) in outcome.projection_failed_ids
    assert outcome.should_hold is True
    assert result["bills_module_synced"] == 1


def test_download_from_qbo_budget_exceeded_propagates():
    """Fix 1: _download_from_qbo must not swallow budget refusal as return None."""
    error = _budget_error()
    attachable = _make_qbo_attachable()
    connector = AttachableAttachmentConnector(auth_service=MagicMock())
    connector.auth_service.ensure_valid_token.return_value = MagicMock(access_token="token")

    with patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.QboAttachableClient"
    ) as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.get_attachable.side_effect = error
        with pytest.raises(QboBudgetExceededError):
            connector._download_from_qbo(attachable, REALM_ID)


def test_sync_to_attachments_budget_exceeded_propagates():
    """Fix 2: _sync_to_attachments must not swallow budget refusal as partial healthy list."""
    error = _budget_error()
    service = QboAttachableService(auth_service=MagicMock())
    attachable = _make_qbo_attachable()

    with patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.AttachableAttachmentConnector"
    ) as mock_connector_cls:
        mock_connector_cls.return_value.sync_from_qbo_attachable.side_effect = error
        with pytest.raises(QboBudgetExceededError):
            service._sync_to_attachments([attachable], REALM_ID)


def test_sync_to_attachments_write_refused_propagates():
    error = _write_refused_error()
    service = QboAttachableService(auth_service=MagicMock())
    attachable = _make_qbo_attachable()

    with patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.AttachableAttachmentConnector"
    ) as mock_connector_cls:
        mock_connector_cls.return_value.sync_from_qbo_attachable.side_effect = error
        with pytest.raises(QboWriteRefusedError):
            service._sync_to_attachments([attachable], REALM_ID)
