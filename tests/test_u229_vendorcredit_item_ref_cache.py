"""U-229 — run-scoped item-ref cache for VendorCredit line resolver."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
    VendorCreditBillCreditConnector,
)
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)

VC_BILL_CREDIT_SERVICE = (
    "integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service"
)


def _make_qbo_vc_line(*, line_id, qbo_line_id, item_ref_value):
    return SimpleNamespace(
        id=line_id,
        qbo_line_id=qbo_line_id,
        amount=Decimal("10.00"),
        customer_ref_value=None,
        item_ref_value=item_ref_value,
        description="line",
        qty=Decimal("1"),
        unit_price=Decimal("10.00"),
        billable_status=None,
    )


def _make_qbo_vc(*, vc_id, qbo_id="VC-1"):
    return SimpleNamespace(
        id=vc_id,
        qbo_id=qbo_id,
        realm_id="realm-1",
        vendor_ref_value="vend-1",
        doc_number=f"VC-{vc_id}",
        txn_date="2026-01-15",
        private_note="memo",
        total_amt=Decimal("30.00"),
    )


def _make_bill_credit(*, bill_credit_id, public_id):
    return SimpleNamespace(
        id=bill_credit_id,
        public_id=public_id,
        row_version="rv1",
        credit_number=f"VC-{bill_credit_id}",
    )


def _build_line_connector_with_item_mocks():
    """Real line connector with a mocked dbo-native SubCostCode resolver injected
    directly. U-307d: item-ref resolution is dbo-native only — SubCostCode.QboId via
    sub_cost_code_service.read_by_qbo_identity; the legacy qbo.Item -> qbo.ItemSubCostCode
    hop (and its qbo_item_repo/item_scc_repo) is gone, so the run-scoped cache is now
    exercised over that single dbo-native lookup."""
    connector = VendorCreditLineItemConnector()
    connector.sub_cost_code_service = Mock()
    connector.bill_credit_line_item_service = Mock()
    connector.bill_credit_line_item_service.repo = Mock()
    connector._get_project_public_id = Mock(return_value=None)
    # U-361: dbo-only line identity — make every line a direct HIT (an existing,
    # realm-complete row) so the line sync is a plain in-place update with no
    # create lock to grant, keeping this file focused on the item-ref cache.
    connector.bill_credit_line_item_service.read_by_qbo_identity.side_effect = (
        lambda bill_credit_id, qbo_id: SimpleNamespace(
            id=1, public_id="bcli-pub", row_version="rv", qbo_id=qbo_id, realm_id="realm-1",
        )
    )
    connector.bill_credit_line_item_service.update_by_public_id.return_value = SimpleNamespace(id=1)
    return connector


def test_run_scoped_item_ref_cache_and_single_line_connector():
    """One line connector instance; item-ref repos queried once per distinct ref across credits."""
    scc_by_ref = {
        "ITEM-A": SimpleNamespace(id=1001),
        "ITEM-B": SimpleNamespace(id=1002),
        "ITEM-C": SimpleNamespace(id=1003),
    }

    line_connector = _build_line_connector_with_item_mocks()
    line_connector.sub_cost_code_service.read_by_qbo_identity.side_effect = (
        lambda ref, realm=None: scc_by_ref.get(ref)
    )

    bill_credit_a = _make_bill_credit(bill_credit_id=10, public_id="bc-pub-10")
    bill_credit_b = _make_bill_credit(bill_credit_id=20, public_id="bc-pub-20")

    bill_credit_service = Mock()
    # U-353: dbo-only HIT — one direct-identity read per sync (no mapping-table hop).
    bill_credit_service.read_by_qbo_identity.side_effect = [bill_credit_a, bill_credit_b]
    bill_credit_service.update_by_public_id.side_effect = [bill_credit_a, bill_credit_b]
    bill_credit_service.repo = Mock()

    connector = VendorCreditBillCreditConnector(
        bill_credit_service=bill_credit_service,
        vendor_service=Mock(),
        reconciliation_repo=Mock(),
        line_item_connector=line_connector,
    )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")

    assert connector.line_item_connector is line_connector

    lines_a = [
        _make_qbo_vc_line(line_id=1, qbo_line_id="L1", item_ref_value="ITEM-A"),
        _make_qbo_vc_line(line_id=2, qbo_line_id="L2", item_ref_value="ITEM-B"),
        _make_qbo_vc_line(line_id=3, qbo_line_id="L3", item_ref_value="ITEM-A"),
    ]
    lines_b = [
        _make_qbo_vc_line(line_id=4, qbo_line_id="L4", item_ref_value="ITEM-B"),
        _make_qbo_vc_line(line_id=5, qbo_line_id="L5", item_ref_value="ITEM-C"),
        _make_qbo_vc_line(line_id=6, qbo_line_id="L6", item_ref_value="ITEM-A"),
    ]

    with patch(f"{VC_BILL_CREDIT_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_vendor_credit(_make_qbo_vc(vc_id=100), lines_a)
        connector.sync_from_qbo_vendor_credit(_make_qbo_vc(vc_id=200, qbo_id="VC-2"), lines_b)

    # Same line connector instance is reused across both credits — its cache carried over.
    assert connector.line_item_connector is line_connector

    distinct_item_refs = {"ITEM-A", "ITEM-B", "ITEM-C"}
    read_by_qbo_identity = line_connector.sub_cost_code_service.read_by_qbo_identity
    assert read_by_qbo_identity.call_count == len(distinct_item_refs)
    assert {
        c.args[0] for c in read_by_qbo_identity.call_args_list
    } == distinct_item_refs


def test_cached_miss_item_ref_memoized():
    """Unresolvable item refs are cached — resolver not re-queried on subsequent lines."""
    connector = _build_line_connector_with_item_mocks()
    connector.sub_cost_code_service.read_by_qbo_identity.return_value = None

    connector._get_sub_cost_code_id("MISSING-ITEM")
    connector._get_sub_cost_code_id("MISSING-ITEM")

    # Second call served from the run-scoped cache — resolver queried once.
    connector.sub_cost_code_service.read_by_qbo_identity.assert_called_once_with("MISSING-ITEM", None)
