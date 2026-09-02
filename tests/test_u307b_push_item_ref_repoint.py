"""U-307b: wire the 3 PUSH consumers' item-ref resolution --
`BillBillConnector._get_qbo_item_ref`, `PurchaseExpenseConnector._get_qbo_item_ref`,
`InvoiceInvoiceConnector._get_qbo_item_ref_for_line` -- off the legacy
`qbo.Item -> qbo.ItemSubCostCode` hop onto U-307a's reverse resolver
(`cost_code_resolver.resolve_qbo_item_ref`): dbo-native `SubCostCode.QboId`
direct, realm-verified, no `qbo.Item` hop.

These tests mock at the `SubCostCodeService` boundary and let the real
resolver run (mirrors test_u296_bill_connector_reference_resolvers.py's
pattern for the sibling term/vendor resolvers) -- they prove the CONNECTOR
wiring (right args in, right adaptation out, realm_id actually threaded from
the top-level sync call down to the resolver). `resolve_qbo_item_ref` itself
is exhaustively covered by test_u307a_cost_code_resolver.py and is not
re-tested here.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


REALM_ID = "realm-1"
OTHER_REALM_ID = "realm-2"


def _make_sub_cost_code(**overrides):
    defaults = dict(id=7, qbo_id="9", realm_id=REALM_ID, name="Concrete")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ============================================================================
# BillBillConnector._get_qbo_item_ref
# ============================================================================


def _make_bill_connector(**overrides):
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    kwargs = dict(
        bill_service=MagicMock(),
        vendor_service=MagicMock(),
        vendor_vendor_repo=MagicMock(),
        qbo_vendor_repo=MagicMock(),
        qbo_bill_repo=MagicMock(),
        qbo_bill_line_repo=MagicMock(),
        bill_line_item_service=MagicMock(),
        reconciliation_repo=MagicMock(),
        company_service=MagicMock(),
        payment_term_service=MagicMock(),
        sub_cost_code_service=MagicMock(),
    )
    kwargs.update(overrides)
    return BillBillConnector(**kwargs)


def test_bill_item_ref_resolves_via_dbo_native_sub_cost_code_no_legacy_touch():
    connector = _make_bill_connector()
    connector.sub_cost_code_service.read_by_id.return_value = _make_sub_cost_code()

    ref = connector._get_qbo_item_ref(7, REALM_ID)

    assert ref.value == "9"
    assert ref.name == "Concrete"
    connector.sub_cost_code_service.read_by_id.assert_called_once_with(7)
    # U-307d: BillBillConnector no longer holds legacy qbo.Item* repos at all —
    # the reverse resolver's freedom from the legacy hop is now structural.


def test_bill_item_ref_none_on_falsy_sub_cost_code_id():
    connector = _make_bill_connector()

    assert connector._get_qbo_item_ref(None, REALM_ID) is None
    assert connector._get_qbo_item_ref(0, REALM_ID) is None
    connector.sub_cost_code_service.read_by_id.assert_not_called()


@pytest.mark.parametrize(
    "sub_cost_code",
    [
        _make_sub_cost_code(qbo_id=None),
        _make_sub_cost_code(realm_id=OTHER_REALM_ID),
        _make_sub_cost_code(realm_id=None),
        None,
    ],
    ids=["no_qbo_id", "realm_mismatch", "null_realm_row", "sub_cost_code_missing"],
)
def test_bill_item_ref_none_cases_with_no_legacy_fallback(sub_cost_code):
    """U-307b design point: the reverse resolver has NO legacy qbo.Item hop --
    every one of these cases (no QboId stamped, wrong realm, NULL realm, no
    such SubCostCode) degrades straight to None, unlike this same connector's
    _get_qbo_sales_term_ref/_get_qbo_vendor_ref, which fall back to their
    legacy mapping tables on a miss."""
    connector = _make_bill_connector()
    connector.sub_cost_code_service.read_by_id.return_value = sub_cost_code

    assert connector._get_qbo_item_ref(7, REALM_ID) is None


def test_bill_build_qbo_line_threads_realm_id_to_item_ref_resolution():
    """Call-arg proof realm_id actually reaches the resolver end to end --
    the exact gap class U-296's Codex review caught (a fully-stubbed resolver
    call hides a silently-dropped arg)."""
    from integrations.intuit.qbo.bill.external.schemas import QboReferenceType

    connector = _make_bill_connector()
    connector._get_qbo_item_ref = MagicMock(return_value=QboReferenceType(value="9", name="Concrete"))
    connector._get_qbo_customer_ref = MagicMock(return_value=None)
    line_item = SimpleNamespace(
        id=1, sub_cost_code_id=7, project_id=None, is_billable=None, is_billed=None,
        markup=None, amount=None, quantity=None, rate=None, description="d",
    )

    connector._build_qbo_line(line_item, 1, REALM_ID)

    connector._get_qbo_item_ref.assert_called_once_with(7, REALM_ID)


def test_bill_build_qbo_line_raises_when_item_ref_unresolved():
    """Preserve the pre-existing fail-loud contract -- a Bill line with a
    SubCostCode that can't be resolved to a QBO Item must dead-letter the
    push, not silently drop the line."""
    connector = _make_bill_connector()
    connector.sub_cost_code_service.read_by_id.return_value = None
    line_item = SimpleNamespace(id=1, sub_cost_code_id=7, project_id=None)

    with pytest.raises(ValueError, match="no QBO Item mapping"):
        connector._build_qbo_line(line_item, 1, REALM_ID)


# ============================================================================
# PurchaseExpenseConnector._get_qbo_item_ref
# ============================================================================


def _make_purchase_connector(**overrides):
    from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector

    kwargs = dict(
        expense_service=MagicMock(),
        vendor_service=MagicMock(),
        vendor_vendor_repo=MagicMock(),
        qbo_vendor_repo=MagicMock(),
        reconciliation_repo=MagicMock(),
        sub_cost_code_service=MagicMock(),
    )
    kwargs.update(overrides)
    return PurchaseExpenseConnector(**kwargs)


def test_purchase_item_ref_resolves_via_dbo_native_sub_cost_code():
    connector = _make_purchase_connector()
    connector.sub_cost_code_service.read_by_id.return_value = _make_sub_cost_code()

    ref = connector._get_qbo_item_ref(7, REALM_ID)

    assert ref.value == "9"
    assert ref.name == "Concrete"
    connector.sub_cost_code_service.read_by_id.assert_called_once_with(7)


@pytest.mark.parametrize(
    "sub_cost_code",
    [_make_sub_cost_code(realm_id=OTHER_REALM_ID), None],
    ids=["realm_mismatch", "sub_cost_code_missing"],
)
def test_purchase_item_ref_none_cases_with_no_legacy_fallback(sub_cost_code):
    connector = _make_purchase_connector()
    connector.sub_cost_code_service.read_by_id.return_value = sub_cost_code

    assert connector._get_qbo_item_ref(7, REALM_ID) is None


def test_purchase_recode_purchase_line_threads_realm_id_to_item_ref_resolution():
    """recode_purchase_line takes realm_id directly (not via a sync method) --
    both of its _get_qbo_item_ref call sites (the already-recoded idempotency
    check and the actual recode) must forward it."""
    connector = _make_purchase_connector()
    connector._get_qbo_item_ref = MagicMock(return_value=SimpleNamespace(value="9", name="Concrete"))
    connector._get_qbo_customer_ref = MagicMock(return_value=None)

    fresh_raw = {
        "SyncToken": "3",
        "Line": [
            {
                "Id": "1",
                "DetailType": "AccountBasedExpenseLineDetail",
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": "58999", "name": "NEED TO CATEGORIZE"},
                },
            }
        ],
    }
    updated_raw = dict(fresh_raw, SyncToken="4")
    mock_client = MagicMock()
    mock_client.get_purchase_raw.return_value = fresh_raw
    mock_client.update_purchase_raw.return_value = updated_raw

    with patch(
        "integrations.intuit.qbo.purchase.external.client.QboPurchaseClient"
    ) as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value = mock_client
        connector.recode_purchase_line(
            realm_id=REALM_ID,
            qbo_purchase_qbo_id="p-1",
            target_qbo_line_id="1",
            sub_cost_code_id=7,
            project_id=None,
            description="d",
            expected_sync_token="3",
        )

    connector._get_qbo_item_ref.assert_called_once_with(7, REALM_ID)


# ============================================================================
# InvoiceInvoiceConnector._get_qbo_item_ref_for_line
# ============================================================================


def _make_invoice_connector(**overrides):
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector

    kwargs = dict(
        invoice_service=MagicMock(),
        project_service=MagicMock(),
        qbo_customer_repo=MagicMock(),
        customer_project_repo=MagicMock(),
        reconciliation_repo=MagicMock(),
        sub_cost_code_service=MagicMock(),
    )
    kwargs.update(overrides)
    return InvoiceInvoiceConnector(**kwargs)


def test_invoice_item_ref_for_line_manual_resolves_via_dbo_native():
    connector = _make_invoice_connector()
    connector.sub_cost_code_service.read_by_id.return_value = _make_sub_cost_code()
    line_item = SimpleNamespace(source_type="Manual", sub_cost_code_id=7)

    ref = connector._get_qbo_item_ref_for_line(line_item, REALM_ID)

    assert ref.value == "9"
    assert ref.name == "Concrete"
    connector.sub_cost_code_service.read_by_id.assert_called_once_with(7)


def test_invoice_item_ref_for_line_manual_none_on_realm_mismatch():
    connector = _make_invoice_connector()
    connector.sub_cost_code_service.read_by_id.return_value = _make_sub_cost_code(realm_id=OTHER_REALM_ID)
    line_item = SimpleNamespace(source_type="Manual", sub_cost_code_id=7)

    assert connector._get_qbo_item_ref_for_line(line_item, REALM_ID) is None


def test_invoice_item_ref_for_line_bill_line_item_walks_to_sub_cost_code():
    connector = _make_invoice_connector()
    connector.sub_cost_code_service.read_by_id.return_value = _make_sub_cost_code()
    line_item = SimpleNamespace(
        source_type="BillLineItem", bill_line_item_id=42,
        expense_line_item_id=None, bill_credit_line_item_id=None,
    )

    with patch(
        "entities.bill_line_item.business.service.BillLineItemService.read_by_id",
        return_value=SimpleNamespace(sub_cost_code_id=7),
    ):
        ref = connector._get_qbo_item_ref_for_line(line_item, REALM_ID)

    assert ref.value == "9"


def test_invoice_item_ref_for_line_none_when_no_sub_cost_code_id():
    connector = _make_invoice_connector()
    line_item = SimpleNamespace(
        source_type="Manual", sub_cost_code_id=None,
    )

    ref = connector._get_qbo_item_ref_for_line(line_item, REALM_ID)

    assert ref is None
    connector.sub_cost_code_service.read_by_id.assert_not_called()


def test_invoice_build_qbo_invoice_line_threads_realm_id_to_item_ref_resolution():
    from integrations.intuit.qbo.invoice.external.schemas import QboReferenceType

    connector = _make_invoice_connector()
    connector._get_qbo_item_ref_for_line = MagicMock(return_value=QboReferenceType(value="9", name="Concrete"))
    line_item = SimpleNamespace(
        id=1, source_type="Manual", sub_cost_code_id=7,
        price=None, amount=100, quantity=None, rate=None, description="d",
    )

    connector._build_qbo_invoice_line(line_item, reimburse_charge_lookup=None, realm_id=REALM_ID)

    connector._get_qbo_item_ref_for_line.assert_called_once_with(line_item, REALM_ID)


def test_invoice_resolve_linked_txn_threads_realm_id_to_item_ref_resolution():
    from integrations.intuit.qbo.invoice.external.schemas import QboReferenceType

    connector = _make_invoice_connector()
    connector._get_qbo_item_ref_for_line = MagicMock(return_value=QboReferenceType(value="9", name="Concrete"))
    line_item = SimpleNamespace(id=1, source_type="Manual", price=None, amount=100)

    # Non-empty lookup so `_resolve_linked_txn_for_line`'s ReimburseCharge-matching
    # branch (the one that resolves an item_ref) is actually entered.
    connector._resolve_linked_txn_for_line(line_item, reimburse_charge_lookup={"k": "v"}, realm_id=REALM_ID)

    connector._get_qbo_item_ref_for_line.assert_called_once_with(line_item, REALM_ID)
