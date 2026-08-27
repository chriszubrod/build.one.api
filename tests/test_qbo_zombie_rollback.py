"""Pure-logic tests for QBO->dbo compensating rollback on line-sync failure."""
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import stub_qbo_identity_fastpath_miss
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
    VendorCreditBillCreditConnector,
)
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)


def _make_qbo_bill(*, bill_id=100, qbo_id="QB-1", total=Decimal("100.00"), realm_id="realm-1"):
    return SimpleNamespace(
        id=bill_id,
        qbo_id=qbo_id,
        realm_id=realm_id,
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
        customer_project_repo=Mock(),
        qbo_customer_repo=Mock(),
        qbo_account_repo=Mock(),
        term_payment_term_repo=Mock(),
        qbo_term_repo=Mock(),
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-id")
    # U-283: these tests exercise the legacy create/rollback path.
    stub_qbo_identity_fastpath_miss(connector.bill_service)
    return connector


def test_rollback_orphan_header_deletes_mapping_before_header():
    """Mapping delete must precede header delete (mapping FK is NO_ACTION)."""
    call_order = []

    rollback_orphan_header(
        delete_header=lambda: call_order.append("header"),
        delete_mapping=lambda: call_order.append("mapping"),
        entity_label="BillCredit",
        entity_id=42,
    )

    assert call_order == ["mapping", "header"]


def test_rollback_orphan_header_header_delete_failure_invokes_callback():
    """Failed header delete after mapping delete invokes on_header_delete_failed."""
    callback = Mock()
    header_exc = RuntimeError("FK 547")

    rollback_orphan_header(
        delete_header=Mock(side_effect=header_exc),
        delete_mapping=Mock(),
        entity_label="BillCredit",
        entity_id=42,
        on_header_delete_failed=callback,
    )

    callback.assert_called_once_with(header_exc)


def test_rollback_orphan_header_both_deletes_fail_does_not_invoke_callback():
    """When mapping delete also failed, header delete failure must not invoke the callback."""
    callback = Mock()
    mapping_exc = RuntimeError("mapping FK 547")
    header_exc = RuntimeError("header FK 547")

    rollback_orphan_header(
        delete_header=Mock(side_effect=header_exc),
        delete_mapping=Mock(side_effect=mapping_exc),
        entity_label="BillCredit",
        entity_id=42,
        on_header_delete_failed=callback,
    )

    callback.assert_not_called()


def _rollback_preserves_original_exception(*, delete_mapping, delete_header, on_header_delete_failed=None):
    """Simulate connector usage: rollback runs in except, then original re-raises."""
    original = RuntimeError("line sync failed")
    with pytest.raises(RuntimeError, match="line sync failed") as exc_info:
        try:
            raise original
        except RuntimeError:
            rollback_orphan_header(
                delete_header=delete_header,
                delete_mapping=delete_mapping,
                entity_label="BillCredit",
                entity_id=42,
                on_header_delete_failed=on_header_delete_failed,
            )
            raise
    assert exc_info.value is original


def test_rollback_orphan_header_both_deletes_fail_preserves_original_exception():
    callback = Mock()
    _rollback_preserves_original_exception(
        delete_mapping=Mock(side_effect=RuntimeError("mapping FK")),
        delete_header=Mock(side_effect=RuntimeError("header FK")),
        on_header_delete_failed=callback,
    )
    callback.assert_not_called()


def test_rollback_orphan_header_header_delete_fail_preserves_original_exception():
    callback = Mock()
    header_exc = RuntimeError("header FK")
    _rollback_preserves_original_exception(
        delete_mapping=Mock(),
        delete_header=Mock(side_effect=header_exc),
        on_header_delete_failed=callback,
    )
    callback.assert_called_once_with(header_exc)


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
        bill_number="INV-EXISTING",  # read by the U-027 preserve decision on UPDATE
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


# --- VendorCredit line connector: unswallow + parent rollback (U-218a) ---


def _make_qbo_vc(*, vc_id=100, qbo_id="VC-1", total=Decimal("100.00"), realm_id="realm-1"):
    return SimpleNamespace(
        id=vc_id,
        qbo_id=qbo_id,
        realm_id=realm_id,
        vendor_ref_value="vend-1",
        doc_number="VC-001",
        txn_date="2026-01-15",
        private_note="memo",
        total_amt=total,
    )


def _make_qbo_vc_line(*, line_id=1, qbo_line_id="line-1", amount=Decimal("100.00")):
    return SimpleNamespace(
        id=line_id,
        qbo_line_id=qbo_line_id,
        amount=amount,
        customer_ref_value=None,
        item_ref_value=None,
        description="line",
        qty=Decimal("1"),
        unit_price=amount,
        billable_status=None,
    )


def _build_vc_connector(**overrides):
    mapping_repo = Mock()
    bill_credit_service = Mock()
    connector = VendorCreditBillCreditConnector(
        mapping_repo=mapping_repo,
        bill_credit_service=bill_credit_service,
        bill_credit_line_item_service=Mock(),
        vendor_service=Mock(),
        reconciliation_repo=Mock(),
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    # U-278: no prior dbo-native identity yet — these rollback/compensation tests
    # exercise the CREATE and mapping-table UPDATE paths, not the fast path. Set on
    # whichever bill_credit_service ended up assigned (default or override) so an
    # overridden Mock doesn't skip this.
    connector.bill_credit_service.read_by_qbo_identity.return_value = None
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-id")
    return connector


def test_vendorcredit_sync_from_qbo_line_propagates_exception():
    """Line connector must raise on projection failure, not return None."""
    connector = VendorCreditLineItemConnector()
    connector.mapping_repo.read_by_qbo_line_id = Mock(return_value=None)
    connector.bill_credit_line_item_service = Mock()
    connector.bill_credit_line_item_service.read_by_bill_credit_id.return_value = []
    connector.bill_credit_line_item_service.create.side_effect = RuntimeError("projection failed")

    qbo_line = _make_qbo_vc_line()
    qbo_line.id = None  # skip fingerprint branch; exercise create path only

    with pytest.raises(RuntimeError, match="projection failed"):
        connector.sync_from_qbo_line(1, "bc-pub-1", qbo_line)


def test_new_vendorcredit_line_sync_failure_compensating_rollback():
    """NEW-credit path: line sync failure deletes mapping then header and re-raises."""
    fake_bc = SimpleNamespace(id=42, public_id="bc-pub-42")
    call_order = []

    mapping_repo = Mock()
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = None
    mapping_repo.delete_by_qbo_vendor_credit_id.side_effect = lambda *_: call_order.append("mapping")

    bill_credit_service = Mock()
    bill_credit_service.create.return_value = fake_bc
    bill_credit_service.delete_by_public_id.side_effect = lambda *_: call_order.append("header")

    connector = _build_vc_connector(mapping_repo=mapping_repo, bill_credit_service=bill_credit_service)
    connector._sync_line_items = Mock(side_effect=RuntimeError("line sync failed"))

    qbo_vc = _make_qbo_vc()
    qbo_lines = [_make_qbo_vc_line()]

    with pytest.raises(RuntimeError, match="line sync failed"):
        connector.sync_from_qbo_vendor_credit(qbo_vc, qbo_lines)

    mapping_repo.delete_by_qbo_vendor_credit_id.assert_called_once_with(qbo_vc.id)
    bill_credit_service.delete_by_public_id.assert_called_once_with("bc-pub-42")
    assert call_order == ["mapping", "header"]


def test_existing_vendorcredit_resync_failure_does_not_compensating_delete():
    """Existing-mapping re-sync: line sync failure must NOT delete the bill credit."""
    existing_mapping = SimpleNamespace(bill_credit_id=42, id=99)
    fake_bc = SimpleNamespace(
        id=42,
        public_id="bc-pub-42",
        row_version="rv1",
        credit_number="VC-EXISTING",
    )

    mapping_repo = Mock()
    mapping_repo.read_by_qbo_vendor_credit_id.return_value = existing_mapping

    bill_credit_service = Mock()
    bill_credit_service.read_by_id.return_value = fake_bc
    bill_credit_service.update_by_public_id.return_value = fake_bc
    bill_credit_service.delete_by_public_id = Mock()

    connector = _build_vc_connector(mapping_repo=mapping_repo, bill_credit_service=bill_credit_service)
    connector._sync_line_items = Mock(side_effect=RuntimeError("line sync failed"))

    qbo_vc = _make_qbo_vc()
    qbo_lines = [_make_qbo_vc_line()]

    with pytest.raises(RuntimeError, match="line sync failed"):
        connector.sync_from_qbo_vendor_credit(qbo_vc, qbo_lines)

    bill_credit_service.delete_by_public_id.assert_not_called()


def test_vendorcredit_sync_line_items_aggregates_failures():
    """_sync_line_items collects N line failures into one RuntimeError."""
    mock_line_connector = Mock()
    mock_line_connector.sync_from_qbo_line.side_effect = [
        SimpleNamespace(id=1),
        RuntimeError("line 2 fail"),
        ValueError("line 3 fail"),
    ]

    connector = _build_vc_connector(line_item_connector=mock_line_connector)
    lines = [
        _make_qbo_vc_line(line_id=1, qbo_line_id="L1"),
        _make_qbo_vc_line(line_id=2, qbo_line_id="L2"),
        _make_qbo_vc_line(line_id=3, qbo_line_id="L3"),
    ]

    with pytest.raises(RuntimeError, match="2 of 3 credit line\\(s\\) failed"):
        connector._sync_line_items(10, "bc-pub-10", lines, "realm-1")

    assert mock_line_connector.sync_from_qbo_line.call_count == 3


QBO_CUSTOMER_REPO_PATH = "integrations.intuit.qbo.customer.persistence.repo.QboCustomerRepository"


# test_vendorcredit_get_project_public_id_db_error_propagates removed U-311
# -- it exercised the legacy qbo.Customer -> qbo.CustomerProject fallback
# hop, which Wave-5 Option A deleted from _resolve_project_public_id
# entirely (there's no longer any code path where a QboCustomerRepository
# error could propagate from this resolver -- a dbo.Project miss/refusal
# just returns None). See tests/test_u278_vendorcredit_qbo_identity_repoint.py
# for this resolver's post-repoint coverage.


def test_vendorcredit_get_project_public_id_not_found_returns_none():
    """Genuine not-found in customer-ref resolver returns None."""
    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = None

    connector = VendorCreditLineItemConnector()
    connector.project_service = Mock(read_by_qbo_identity=Mock(return_value=None))

    with patch(QBO_CUSTOMER_REPO_PATH, return_value=qbo_customer_repo):
        assert connector._get_project_public_id("QBO-100") is None


def test_vendorcredit_get_sub_cost_code_id_db_error_propagates():
    """A DB error inside the dbo-native item-ref resolver must propagate."""
    connector = VendorCreditLineItemConnector()
    connector.sub_cost_code_service = Mock()
    connector.sub_cost_code_service.read_by_qbo_identity.side_effect = ValueError("db blip")

    with pytest.raises(ValueError, match="db blip"):
        connector._get_sub_cost_code_id("ITEM-1")


def test_vendorcredit_get_sub_cost_code_id_not_found_returns_none():
    """A genuine dbo-native miss in the item-ref resolver returns None."""
    connector = VendorCreditLineItemConnector()
    connector.sub_cost_code_service = Mock()
    connector.sub_cost_code_service.read_by_qbo_identity.return_value = None

    assert connector._get_sub_cost_code_id("ITEM-1") is None


# U-307d retired 2 tests here that exercised the legacy qbo.Item -> qbo.ItemSubCostCode
# hop (dangling-mapping-returns-None, and a second DB-error propagation from the
# SubCostCode read_by_id existence check the legacy hop performed). The hop is gone —
# resolution is a single dbo-native read_by_qbo_identity — so those scenarios are
# unreachable; the dbo-native miss + dbo-native DB-error tests above cover it.
