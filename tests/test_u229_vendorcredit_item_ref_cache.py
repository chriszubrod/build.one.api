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
from conftest import stub_qbo_identity_fastpath_miss

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
    """Real line connector with mocked item-ref resolution repos (injected directly,
    not via @patch, so it's robust to where QboItemRepository/ItemSubCostCodeRepository
    happen to be imported)."""
    connector = VendorCreditLineItemConnector()
    connector.qbo_item_repo = Mock()
    connector.item_scc_repo = Mock()
    # U-307a: the dbo-native primary lookup (SubCostCode.QboId) must explicitly miss
    # so resolution falls through to the legacy qbo.Item -> qbo.ItemSubCostCode hop
    # this file exercises — an unstubbed Mock()'s auto-truthy `.read_by_qbo_identity`
    # would otherwise short-circuit before qbo_item_repo/item_scc_repo are ever touched.
    connector.sub_cost_code_service = Mock()
    connector.sub_cost_code_service.read_by_qbo_identity.return_value = None
    connector.mapping_repo = Mock()
    connector.mapping_repo.read_by_qbo_line_id.return_value = None
    connector.bill_credit_line_item_service = Mock()
    connector.bill_credit_line_item_service.read_by_bill_credit_id.return_value = []
    connector.bill_credit_line_item_service.create.return_value = SimpleNamespace(id=1)
    connector.bill_credit_line_item_service.repo = Mock()
    connector._get_project_public_id = Mock(return_value=None)
    # This file exercises the item-ref cache via the legacy mapping-table path,
    # not the U-293b dbo-native fast path — force a miss so a bare Mock's
    # auto-truthy `.read_by_qbo_identity(...)` doesn't silently divert it.
    stub_qbo_identity_fastpath_miss(connector.bill_credit_line_item_service)
    return connector


def test_run_scoped_item_ref_cache_and_single_line_connector():
    """One line connector instance; item-ref repos queried once per distinct ref across credits."""
    qbo_items = {
        "ITEM-A": SimpleNamespace(id=101),
        "ITEM-B": SimpleNamespace(id=102),
        "ITEM-C": SimpleNamespace(id=103),
    }
    scc_mappings = {
        101: SimpleNamespace(sub_cost_code_id=1001),
        102: SimpleNamespace(sub_cost_code_id=1002),
        103: SimpleNamespace(sub_cost_code_id=1003),
    }

    line_connector = _build_line_connector_with_item_mocks()
    line_connector.qbo_item_repo.read_by_qbo_id.side_effect = lambda qbo_id: qbo_items.get(qbo_id)
    line_connector.item_scc_repo.read_by_qbo_item_id.side_effect = lambda qid: scc_mappings.get(qid)
    line_connector.sub_cost_code_service.read_by_id.return_value = SimpleNamespace(id=1)

    existing_mapping = SimpleNamespace(bill_credit_id=10, id=1)
    bill_credit_a = _make_bill_credit(bill_credit_id=10, public_id="bc-pub-10")
    bill_credit_b = _make_bill_credit(bill_credit_id=20, public_id="bc-pub-20")

    mapping_repo = Mock()
    mapping_repo.read_by_qbo_vendor_credit_id.side_effect = [
        existing_mapping,
        SimpleNamespace(bill_credit_id=20, id=2),
    ]

    bill_credit_service = Mock()
    bill_credit_service.read_by_id.side_effect = [bill_credit_a, bill_credit_b]
    bill_credit_service.update_by_public_id.side_effect = [bill_credit_a, bill_credit_b]
    bill_credit_service.repo = Mock()
    # U-278: no prior dbo-native identity yet — this test exercises the mapping-table
    # UPDATE path across two sequential syncs.
    bill_credit_service.read_by_qbo_identity.return_value = None

    connector = VendorCreditBillCreditConnector(
        mapping_repo=mapping_repo,
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
    assert line_connector.qbo_item_repo.read_by_qbo_id.call_count == len(distinct_item_refs)
    assert line_connector.item_scc_repo.read_by_qbo_item_id.call_count == len(distinct_item_refs)
    assert {
        c.args[0] for c in line_connector.qbo_item_repo.read_by_qbo_id.call_args_list
    } == distinct_item_refs


def test_cached_miss_item_ref_memoized():
    """Unresolvable item refs are cached — resolver not re-queried on subsequent lines."""
    connector = _build_line_connector_with_item_mocks()
    connector.qbo_item_repo.read_by_qbo_id.return_value = None

    connector._get_sub_cost_code_id("MISSING-ITEM")
    connector._get_sub_cost_code_id("MISSING-ITEM")

    connector.qbo_item_repo.read_by_qbo_id.assert_called_once_with("MISSING-ITEM")
    connector.item_scc_repo.read_by_qbo_item_id.assert_not_called()
