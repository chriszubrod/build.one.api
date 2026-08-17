"""sync_qbo_to_local must not serialize full Bill payloads unless explicitly requested (U-217 follow-up k)."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.bill.business.model import QboBill
from scripts.sync_qbo_bill import sync_qbo_to_local

REALM_ID = "realm-test"


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


def _run_sync_without_attachments(*, include_bill_payload=False):
    bill = _make_qbo_bill()
    outcome = SyncOutcome.for_service_pull()
    outcome.synced = [bill]

    qbo_bill_service = MagicMock()
    qbo_bill_service.sync_from_qbo.return_value = outcome

    bill_connector = MagicMock()
    bill_connector.sync_from_qbo_bill.return_value = SimpleNamespace(
        id=99, public_id="22222222-2222-2222-2222-222222222222"
    )

    with patch("scripts.sync_qbo_bill.BillService") as mock_bill_svc_cls, patch(
        "scripts.sync_qbo_bill.BillLineItemService"
    ) as mock_bli_svc_cls, patch(
        "scripts.sync_qbo_bill.read_lines_riding_out_race", return_value=[]
    ), patch(
        "scripts.sync_qbo_bill.with_retry",
        side_effect=lambda fn, *args, **kwargs: fn(*args),
    ):
        mock_bill_svc_cls.return_value = MagicMock()
        mock_bli_svc_cls.return_value = MagicMock()

        result, returned_outcome = sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_bill_service=qbo_bill_service,
            bill_connector=bill_connector,
            sync_attachments=False,
            include_bill_payload=include_bill_payload,
        )

    return result, returned_outcome, bill


def test_sync_qbo_to_local_default_omits_bill_payload():
    result, outcome, bill = _run_sync_without_attachments(include_bill_payload=False)

    assert outcome.synced == [bill]
    assert result["bills_synced"] == 1
    assert result["bills_module_synced"] == 1
    assert result["bills"] == []


def test_sync_qbo_to_local_include_bill_payload_returns_full_list():
    result, outcome, bill = _run_sync_without_attachments(include_bill_payload=True)

    assert outcome.synced == [bill]
    assert result["bills_synced"] == 1
    assert result["bills_module_synced"] == 1
    assert result["bills"] == [bill.to_dict()]
