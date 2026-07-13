"""Pure-logic tests for QBO->dbo compensating rollback on line-sync failure."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector


def _make_qbo_bill(*, bill_id=100, qbo_id="QB-1", total=Decimal("100.00")):
    return SimpleNamespace(
        id=bill_id,
        qbo_id=qbo_id,
        vendor_ref_value="vend-1",
        doc_number="INV-001",
        txn_date="2026-01-15",
        due_date="2026-02-15",
        private_note="memo",
        total_amt=total,
    )


def _make_qbo_bill_line(*, line_id=1, amount=Decimal("100.00")):
    return SimpleNamespace(id=line_id, amount=amount)


def _build_connector(**overrides):
    mapping_repo = Mock()
    bill_service = Mock()
    connector = BillBillConnector(
        mapping_repo=mapping_repo,
        bill_service=bill_service,
        vendor_service=Mock(),
        vendor_vendor_repo=Mock(),
        qbo_vendor_repo=Mock(),
        qbo_bill_repo=Mock(),
        qbo_bill_line_repo=Mock(),
        bill_line_item_service=Mock(),
        item_sub_cost_code_repo=Mock(),
        qbo_item_repo=Mock(),
        customer_project_repo=Mock(),
        qbo_customer_repo=Mock(),
        qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(),
        qbo_term_repo=Mock(),
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-id")
    return connector


def test_new_bill_line_sync_failure_compensating_rollback():
    """NEW-bill path: line sync failure deletes header + mapping and re-raises."""
    fake_bill = SimpleNamespace(id=42, public_id="bill-pub-42")
    fake_mapping = SimpleNamespace(id=99)

    mapping_repo = Mock()
    mapping_repo.read_by_qbo_bill_id.return_value = None
    mapping_repo.read_by_bill_id.return_value = fake_mapping

    bill_service = Mock()
    bill_service.create.return_value = fake_bill
    bill_service.delete_by_public_id = Mock()

    connector = _build_connector(mapping_repo=mapping_repo, bill_service=bill_service)
    connector.create_mapping = Mock(return_value=fake_mapping)
    connector._sync_line_items = Mock(side_effect=RuntimeError("line sync failed"))

    qbo_bill = _make_qbo_bill()
    qbo_lines = [_make_qbo_bill_line()]

    with pytest.raises(RuntimeError, match="line sync failed"):
        connector.sync_from_qbo_bill(qbo_bill, qbo_lines)

    bill_service.delete_by_public_id.assert_called_once_with("bill-pub-42")
    mapping_repo.delete_by_id.assert_called_once_with(99)


def test_new_bill_successful_onboarding_no_rollback():
    """NEW-bill path: successful line sync leaves header intact."""
    fake_bill = SimpleNamespace(id=42, public_id="bill-pub-42")

    mapping_repo = Mock()
    mapping_repo.read_by_qbo_bill_id.return_value = None

    bill_service = Mock()
    bill_service.create.return_value = fake_bill
    bill_service.delete_by_public_id = Mock()

    connector = _build_connector(mapping_repo=mapping_repo, bill_service=bill_service)
    connector.create_mapping = Mock(return_value=SimpleNamespace(id=99))
    connector._sync_line_items = Mock()

    qbo_bill = _make_qbo_bill()
    qbo_lines = [_make_qbo_bill_line()]

    result = connector.sync_from_qbo_bill(qbo_bill, qbo_lines)

    assert result is fake_bill
    bill_service.delete_by_public_id.assert_not_called()
    mapping_repo.delete_by_id.assert_not_called()


def test_new_bill_mapping_read_failure_still_raises_original_and_deletes_header():
    '''If the rollback mapping READ raises, the ORIGINAL line-sync error must still propagate
    and the header delete must still be attempted (mapping read failure is logged, not masking).'''
    fake_bill = SimpleNamespace(id=42, public_id='bill-pub-42')
    mapping_repo = Mock()
    mapping_repo.read_by_qbo_bill_id.return_value = None
    mapping_repo.read_by_bill_id.side_effect = ValueError('db blip on mapping read')
    bill_service = Mock()
    bill_service.create.return_value = fake_bill
    bill_service.delete_by_public_id = Mock()
    connector = _build_connector(mapping_repo=mapping_repo, bill_service=bill_service)
    connector.create_mapping = Mock(return_value=SimpleNamespace(id=99))
    connector._sync_line_items = Mock(side_effect=RuntimeError('line sync failed'))
    qbo_bill = _make_qbo_bill()
    qbo_lines = [_make_qbo_bill_line()]
    with pytest.raises(RuntimeError, match='line sync failed'):
        connector.sync_from_qbo_bill(qbo_bill, qbo_lines)
    bill_service.delete_by_public_id.assert_called_once_with('bill-pub-42')


def test_existing_mapping_resync_failure_does_not_compensating_delete():
    """Existing-mapping re-sync: line sync failure must NOT delete the bill."""
    existing_mapping = SimpleNamespace(bill_id=42, id=99)
    fake_bill = SimpleNamespace(
        id=42,
        public_id="bill-pub-42",
        row_version="rv1",
    )

    mapping_repo = Mock()
    mapping_repo.read_by_qbo_bill_id.return_value = existing_mapping

    bill_service = Mock()
    bill_service.read_by_id.return_value = fake_bill
    bill_service.update_by_public_id.return_value = fake_bill
    bill_service.delete_by_public_id = Mock()

    connector = _build_connector(mapping_repo=mapping_repo, bill_service=bill_service)
    connector._sync_line_items = Mock(side_effect=RuntimeError("line sync failed"))

    qbo_bill = _make_qbo_bill()
    qbo_lines = [_make_qbo_bill_line()]

    with pytest.raises(RuntimeError, match="line sync failed"):
        connector.sync_from_qbo_bill(qbo_bill, qbo_lines)

    bill_service.delete_by_public_id.assert_not_called()
    mapping_repo.delete_by_id.assert_not_called()
