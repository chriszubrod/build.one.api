"""U-488: recode_purchase_line carries Qty, UnitPrice, and BillableStatus when safe."""
import copy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.errors import PurchaseChangedInQboError
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType

# Reuse helpers from the canonical recode test module
from tests.test_expense_recode import (
    CATEGORIZE_ACCOUNT,
    FAKE_CUSTOMER_REF,
    FAKE_ITEM_REF,
    PURCHASE_QBO_ID,
    REALM_ID,
    SUB_COST_CODE_ID,
    SYNC_TOKEN,
    TARGET_LINE_ID,
    _build_connector,
    _make_categorize_line_dict,
    _make_item_sibling_dict,
    _make_raw_purchase,
    _patch_raw_client,
)

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"


def _call_recode(connector, **overrides):
    kwargs = {
        "realm_id": REALM_ID,
        "qbo_purchase_qbo_id": PURCHASE_QBO_ID,
        "target_qbo_line_id": TARGET_LINE_ID,
        "sub_cost_code_id": SUB_COST_CODE_ID,
        "project_id": 202,
        "description": "recode desc",
        "expected_sync_token": SYNC_TOKEN,
    }
    kwargs.update(overrides)
    return connector.recode_purchase_line(**kwargs)


def _posted_target_detail(mock_client) -> dict:
    posted = mock_client.update_purchase_raw.call_args[0][0]
    return posted["Line"][0]["ItemBasedExpenseLineDetail"]


def _posted_target_line(mock_client) -> dict:
    posted = mock_client.update_purchase_raw.call_args[0][0]
    return posted["Line"][0]


# --- 1. Reconciling economics ARE sent ---


def test_reconciling_economics_qty_and_unit_price_sent_amount_unchanged():
    amount = "21.94"
    fresh = _make_raw_purchase(lines=[_make_categorize_line_dict(amount=amount)])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        result = _call_recode(
            connector,
            quantity=1,
            rate=Decimal("21.94"),
            amount=Decimal("21.94"),
            is_billable=True,
        )

    assert result["status"] == "written"
    line = _posted_target_line(mock_client)
    assert line["Amount"] == amount
    detail = line["ItemBasedExpenseLineDetail"]
    assert "Qty" in detail
    assert "UnitPrice" in detail
    assert Decimal(str(detail["UnitPrice"])) == Decimal("21.94")
    assert Decimal(str(detail["Qty"])) == Decimal("1")


# --- 2. NON-reconciling economics are NOT sent ---


@pytest.mark.parametrize(
    "quantity,rate,amount",
    [
        (2, Decimal("10.00"), Decimal("21.94")),
        (1, Decimal("21.95"), Decimal("21.94")),
        (1, Decimal("21.93"), Decimal("21.94")),
        (3, Decimal("7.31"), Decimal("21.94")),
    ],
    ids=["qty_times_rate_mismatch", "one_cent_high", "one_cent_low", "rounding_mismatch"],
)
def test_non_reconciling_economics_omit_qty_and_unit_price(quantity, rate, amount):
    amount_str = str(amount)
    fresh = _make_raw_purchase(lines=[_make_categorize_line_dict(amount=amount_str)])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(
            connector,
            quantity=quantity,
            rate=rate,
            amount=amount,
        )

    line = _posted_target_line(mock_client)
    assert line["Amount"] == amount_str
    detail = line["ItemBasedExpenseLineDetail"]
    assert "Qty" not in detail
    assert "UnitPrice" not in detail


# --- 3. Never one without the other ---


@pytest.mark.parametrize(
    "quantity,rate",
    [(None, Decimal("10.00")), (1, None)],
)
def test_partial_qty_or_rate_omits_both(quantity, rate):
    fresh = _make_raw_purchase(lines=[_make_categorize_line_dict(amount="21.94")])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(
            connector,
            quantity=quantity,
            rate=rate,
            amount=Decimal("21.94"),
        )

    detail = _posted_target_detail(mock_client)
    assert "Qty" not in detail
    assert "UnitPrice" not in detail


# --- 4. BillableStatus from local is_billable ---


@pytest.mark.parametrize(
    "is_billable,expected_status",
    [
        (True, "Billable"),
        (False, "NotBillable"),
    ],
)
def test_billable_status_from_local_is_billable(is_billable, expected_status):
    fresh = _make_raw_purchase(
        lines=[
            _make_categorize_line_dict(
                extra_detail={"BillableStatus": "NotBillable"},
            )
        ]
    )
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(connector, is_billable=is_billable)

    detail = _posted_target_detail(mock_client)
    assert detail["BillableStatus"] == expected_status


def test_billable_status_none_carries_forward_from_account_detail():
    fresh = _make_raw_purchase(
        lines=[
            _make_categorize_line_dict(
                extra_detail={"BillableStatus": "Billable"},
            )
        ]
    )
    connector = _build_connector(customer_ref=FAKE_CUSTOMER_REF)

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(connector, is_billable=None)

    detail = _posted_target_detail(mock_client)
    assert detail["BillableStatus"] == "Billable"


# --- 5. Billable downgraded without CustomerRef ---


def test_billable_downgraded_without_customer_ref():
    fresh = _make_raw_purchase(lines=[_make_categorize_line_dict()])
    connector = _build_connector(customer_ref=None)
    connector._get_qbo_customer_ref = MagicMock(return_value=None)

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(connector, project_id=None, is_billable=True)

    detail = _posted_target_detail(mock_client)
    assert "CustomerRef" not in detail
    assert detail["BillableStatus"] == "NotBillable"


# --- 6. ClassRef / TaxCodeRef / MarkupInfo carry; BillableStatus not blind-carried ---


def test_carry_forward_refs_excludes_blind_billable_status():
    markup = {"PriceLevelRef": {"value": "1"}, "Percent": 10}
    fresh = _make_raw_purchase(
        lines=[
            _make_categorize_line_dict(
                extra_detail={
                    "ClassRef": {"value": "class-1", "name": "Phase 1"},
                    "TaxCodeRef": {"value": "NON", "name": "Non"},
                    "MarkupInfo": markup,
                    "BillableStatus": "NotBillable",
                }
            )
        ]
    )
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(connector, is_billable=True)

    detail = _posted_target_detail(mock_client)
    assert detail["ClassRef"] == {"value": "class-1", "name": "Phase 1"}
    assert detail["TaxCodeRef"] == {"value": "NON", "name": "Non"}
    assert detail["MarkupInfo"] == markup
    assert detail["BillableStatus"] == "Billable"


# --- 7. Handler: unresolvable local line still recodes ---


def _make_coding_item(**overrides):
    base = {
        "public_id": PUBLIC_ID,
        "status": "enqueued",
        "qbo_purchase_qbo_id": PURCHASE_QBO_ID,
        "qbo_line_id": TARGET_LINE_ID,
        "qbo_purchase_line_id": 999,
        "confirmed_sub_cost_code_id": SUB_COST_CODE_ID,
        "confirmed_project_id": 202,
        "confirmed_description": "recode desc",
        "sync_token_at_suggest": SYNC_TOKEN,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_outbox_row():
    return QboOutbox(
        id=1,
        public_id="outbox-1",
        row_version="abc",
        kind="recode_purchase_line",
        entity_type="ExpenseCodingItem",
        entity_public_id=PUBLIC_ID,
        realm_id=REALM_ID,
        request_id="req-1",
        status="processing",
        attempts=0,
    )


@patch(
    "integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo.PurchaseLineExpenseLineItemRepository"
)
@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_handler_unresolvable_local_line_still_recodes(
    mock_svc_cls, mock_connector_cls, mock_mapping_repo_cls
):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_coding_item()

    mock_mapping_repo_cls.return_value.read_by_qbo_purchase_line_id.return_value = None

    connector = MagicMock()
    mock_connector_cls.return_value = connector
    connector.recode_purchase_line.return_value = {"status": "written", "sync_token": "6"}

    QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    connector.recode_purchase_line.assert_called_once()
    kwargs = connector.recode_purchase_line.call_args.kwargs
    assert kwargs.get("quantity") is None
    assert kwargs.get("rate") is None
    assert kwargs.get("amount") is None
    assert kwargs.get("is_billable") is None
    svc.mark_written.assert_called_once()


@patch(
    "integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo.PurchaseLineExpenseLineItemRepository"
)
@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_handler_resolves_local_line_economics(
    mock_svc_cls, mock_connector_cls, mock_mapping_repo_cls
):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_coding_item()

    mock_mapping_repo_cls.return_value.read_by_qbo_purchase_line_id.return_value = SimpleNamespace(
        expense_line_item_id=55
    )

    line = SimpleNamespace(
        quantity=1,
        rate=Decimal("21.94"),
        amount=Decimal("21.94"),
        is_billable=True,
    )

    with patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as mock_eli_svc_cls:
        mock_eli_svc_cls.return_value.read_by_id.return_value = line

        connector = MagicMock()
        mock_connector_cls.return_value = connector
        connector.recode_purchase_line.return_value = {"status": "written", "sync_token": "6"}

        QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    kwargs = connector.recode_purchase_line.call_args.kwargs
    assert kwargs["quantity"] == 1
    assert kwargs["rate"] == Decimal("21.94")
    assert kwargs["amount"] == Decimal("21.94")
    assert kwargs["is_billable"] is True


# --- 8. Untouched behaviour ---


def test_line_id_and_sibling_byte_identical_amount_never_assigned():
    sibling = _make_item_sibling_dict()
    original_sibling = copy.deepcopy(sibling)
    amount = "99.99"
    fresh = _make_raw_purchase(
        lines=[_make_categorize_line_dict(amount=amount, line_id=TARGET_LINE_ID), sibling]
    )
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(
            connector,
            quantity=1,
            rate=Decimal("99.99"),
            amount=Decimal("99.99"),
        )

    posted = mock_client.update_purchase_raw.call_args[0][0]
    assert posted["Line"][0]["Id"] == TARGET_LINE_ID
    assert posted["Line"][0]["Amount"] == amount
    assert posted["Line"][1] == original_sibling


def test_sync_token_fail_closed_still_fires():
    fresh = _make_raw_purchase(sync_token="9", lines=[_make_categorize_line_dict()])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        with pytest.raises(PurchaseChangedInQboError):
            _call_recode(
                connector,
                expected_sync_token=SYNC_TOKEN,
                quantity=1,
                rate=Decimal("10"),
                amount=Decimal("10"),
            )

    mock_client.update_purchase_raw.assert_not_called()
