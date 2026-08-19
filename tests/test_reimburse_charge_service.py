"""U-280 — QboReimburseChargeService._upsert create/update kwarg wiring.

Regression coverage for the U-280 signature shrink (SourceTxnType/SourceTxnId/
SourceTxnLineId retired): proves the service still calls the repo's create()
and update_by_qbo_id() with exactly the fields both sides now agree on — a
reintroduced param on one side and not the other fails loud (TypeError) rather
than silently, since neither method accepts **kwargs.
"""
from decimal import Decimal
from unittest.mock import MagicMock, create_autospec

from integrations.intuit.qbo.reimburse_charge.business.service import QboReimburseChargeService
from integrations.intuit.qbo.reimburse_charge.persistence.repo import QboReimburseChargeRepository

REALM_ID = "realm-test"

_PARSED = {
    "qbo_id": "900",
    "customer_ref_value": "77",
    "customer_ref_name": "Haverford",
    "txn_date": "2026-07-01",
    "amount": Decimal("1577.45"),
    "has_been_invoiced": False,
}


def test_upsert_creates_when_no_existing_record():
    # create_autospec (not plain spec=) so a kwarg mismatch against the real
    # create() signature raises TypeError instead of silently passing.
    svc = QboReimburseChargeService(repo=create_autospec(QboReimburseChargeRepository, instance=True))
    svc.repo.read_by_qbo_id_and_realm_id.return_value = None

    svc._upsert(dict(_PARSED), REALM_ID)

    svc.repo.create.assert_called_once_with(
        qbo_id="900",
        realm_id=REALM_ID,
        customer_ref_value="77",
        customer_ref_name="Haverford",
        txn_date="2026-07-01",
        amount=Decimal("1577.45"),
        has_been_invoiced=False,
    )
    svc.repo.update_by_qbo_id.assert_not_called()


def test_upsert_updates_when_existing_record_found():
    svc = QboReimburseChargeService(repo=create_autospec(QboReimburseChargeRepository, instance=True))
    existing = MagicMock(row_version_bytes=b"rv-bytes")
    svc.repo.read_by_qbo_id_and_realm_id.return_value = existing

    svc._upsert(dict(_PARSED), REALM_ID)

    svc.repo.update_by_qbo_id.assert_called_once_with(
        qbo_id="900",
        row_version=b"rv-bytes",
        realm_id=REALM_ID,
        customer_ref_value="77",
        customer_ref_name="Haverford",
        txn_date="2026-07-01",
        amount=Decimal("1577.45"),
        has_been_invoiced=False,
    )
    svc.repo.create.assert_not_called()
