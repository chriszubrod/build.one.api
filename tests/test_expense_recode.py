"""Pure-logic unit tests for surgical QBO Purchase line recoding (Phase C1, raw JSON path)."""
import copy
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError
from integrations.intuit.qbo.purchase.connector.expense.business.errors import (
    PurchaseChangedInQboError,
    PurchaseRecodeMappingError,
)
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType

CATEGORIZE_ACCOUNT = {
    "value": "58999",
    "name": "Cost of construction : NEED TO CATEGORIZE",
}

FAKE_ITEM_REF = QboReferenceType(value="item-42", name="Framing Labor")
FAKE_CUSTOMER_REF = QboReferenceType(value="cust-99", name="TB3 - 917 Tyne Blvd")
FOREIGN_ITEM_REF = QboReferenceType(value="item-99", name="Existing Item")

REALM_ID = "realm-test"
PURCHASE_QBO_ID = "purchase-123"
TARGET_LINE_ID = "1"
SYNC_TOKEN = "5"
SUB_COST_CODE_ID = 101
PROJECT_ID = 202


def _make_categorize_line_dict(
    *,
    line_id: str = TARGET_LINE_ID,
    line_num: int = 1,
    amount: str = "123.45",
    description: str = "orig desc",
    extra_detail: dict | None = None,
) -> dict:
    detail = {"AccountRef": CATEGORIZE_ACCOUNT}
    if extra_detail:
        detail.update(extra_detail)
    return {
        "Id": line_id,
        "LineNum": line_num,
        "Description": description,
        "Amount": amount,
        "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": detail,
    }


def _make_item_sibling_dict(
    *,
    line_id: str = "2",
    line_num: int = 2,
    amount: str = "50.00",
    description: str = "sibling line",
    unmodeled: dict | None = None,
) -> dict:
    line = {
        "Id": line_id,
        "LineNum": line_num,
        "Description": description,
        "Amount": amount,
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": "item-99", "name": "Existing Item"},
            "CustomerRef": {"value": "cust-88", "name": "Other Project"},
            "BillableStatus": "Billable",
            "Qty": 1,
            "UnitPrice": amount,
        },
    }
    if unmodeled:
        line.update(unmodeled)
    return line


def _make_raw_purchase(
    *,
    sync_token: str = SYNC_TOKEN,
    lines: list | None = None,
    extra_header: dict | None = None,
) -> dict:
    raw = {
        "Id": PURCHASE_QBO_ID,
        "SyncToken": sync_token,
        "PaymentType": "CreditCard",
        "AccountRef": {"value": "acct-1", "name": "Ramp Card"},
        "EntityRef": {"value": "vendor-1", "name": "Test Vendor"},
        "Credit": False,
        "TxnDate": "2026-07-01",
        "DocNumber": "RMP-001",
        "PrivateNote": "private note",
        "CurrencyRef": {"value": "USD", "name": "United States Dollar"},
        "DepartmentRef": {"value": "dept-1", "name": "Construction"},
        "GlobalTaxCalculation": "TaxExcluded",
        "Line": lines or [],
    }
    if extra_header:
        raw.update(extra_header)
    return raw


def _build_connector(*, item_ref=FAKE_ITEM_REF, customer_ref=FAKE_CUSTOMER_REF) -> PurchaseExpenseConnector:
    connector = PurchaseExpenseConnector(
        mapping_repo=MagicMock(),
        expense_service=MagicMock(),
        vendor_service=MagicMock(),
        vendor_vendor_repo=MagicMock(),
        qbo_vendor_repo=MagicMock(),
        qbo_purchase_repo=MagicMock(),
        qbo_purchase_line_repo=MagicMock(),
    )
    connector._get_qbo_item_ref = MagicMock(return_value=item_ref)
    connector._get_qbo_customer_ref = MagicMock(return_value=customer_ref)
    return connector


def _patch_raw_client(*, fresh: dict, updated: dict | None = None):
    mock_client = MagicMock()
    mock_client.get_purchase_raw.return_value = copy.deepcopy(fresh)
    bumped = copy.deepcopy(fresh)
    bumped["SyncToken"] = str(int(fresh["SyncToken"]) + 1)
    mock_client.update_purchase_raw.return_value = updated or bumped
    mock_client_class = MagicMock()
    mock_client_class.return_value.__enter__.return_value = mock_client
    mock_client_class.return_value.__exit__.return_value = None
    return patch(
        "integrations.intuit.qbo.purchase.external.client.QboPurchaseClient",
        mock_client_class,
    ), mock_client


def _call_recode(connector, **overrides):
    kwargs = {
        "realm_id": REALM_ID,
        "qbo_purchase_qbo_id": PURCHASE_QBO_ID,
        "target_qbo_line_id": TARGET_LINE_ID,
        "sub_cost_code_id": SUB_COST_CODE_ID,
        "project_id": PROJECT_ID,
        "description": "recode desc",
        "expected_sync_token": SYNC_TOKEN,
    }
    kwargs.update(overrides)
    return connector.recode_purchase_line(**kwargs)


def test_single_line_happy_path_written():
    """One 58999 line recodes to ItemBased; amount and id preserved byte-identically."""
    target_amount = "123.45"
    fresh = _make_raw_purchase(lines=[_make_categorize_line_dict(amount=target_amount)])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        result = _call_recode(connector)

    assert result["status"] == "written"
    assert result["qbo_purchase_qbo_id"] == PURCHASE_QBO_ID
    assert result["target_qbo_line_id"] == TARGET_LINE_ID
    assert result["sync_token"] == "6"

    mock_client.update_purchase_raw.assert_called_once()
    posted = mock_client.update_purchase_raw.call_args[0][0]
    assert posted["SyncToken"] == SYNC_TOKEN
    assert len(posted["Line"]) == 1

    target_line = posted["Line"][0]
    assert target_line["Id"] == TARGET_LINE_ID
    assert target_line["Amount"] == target_amount
    assert target_line["LineNum"] == 1
    assert target_line["Description"] == "recode desc"
    assert target_line["DetailType"] == "ItemBasedExpenseLineDetail"
    assert "AccountBasedExpenseLineDetail" not in target_line

    detail = target_line["ItemBasedExpenseLineDetail"]
    assert detail["ItemRef"] == {"value": FAKE_ITEM_REF.value, "name": FAKE_ITEM_REF.name}
    assert detail["CustomerRef"] == {"value": FAKE_CUSTOMER_REF.value, "name": FAKE_CUSTOMER_REF.name}


def test_sibling_byte_identical_unmodeled_fields_preserved():
    """Sibling line with unmodeled fields is posted exactly as received."""
    sibling = _make_item_sibling_dict(
        unmodeled={
            "LinkedTxn": [{"TxnId": "txn-1", "TxnType": "Bill"}],
            "CustomField": [{"DefinitionId": "1", "StringValue": "keep-me"}],
        }
    )
    original_sibling = copy.deepcopy(sibling)
    fresh = _make_raw_purchase(lines=[_make_categorize_line_dict(), sibling])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(connector)

    posted = mock_client.update_purchase_raw.call_args[0][0]
    assert len(posted["Line"]) == 2
    assert posted["Line"][1] == original_sibling


def test_header_preserved_on_update():
    """ExchangeRate, TxnTaxDetail, and Credit survive the full raw round-trip."""
    fresh = _make_raw_purchase(
        lines=[_make_categorize_line_dict()],
        extra_header={
            "Credit": True,
            "ExchangeRate": 1.25,
            "TxnTaxDetail": {"TotalTax": 0, "TaxLine": []},
        },
    )
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(connector)

    posted = mock_client.update_purchase_raw.call_args[0][0]
    assert posted["Credit"] is True
    assert posted["ExchangeRate"] == 1.25
    assert posted["TxnTaxDetail"] == {"TotalTax": 0, "TaxLine": []}


def test_target_detail_carry_over_from_account_detail():
    """ClassRef, TaxCodeRef, and BillableStatus carry into ItemBasedExpenseLineDetail."""
    fresh = _make_raw_purchase(
        lines=[
            _make_categorize_line_dict(
                extra_detail={
                    "ClassRef": {"value": "class-1", "name": "Phase 1"},
                    "TaxCodeRef": {"value": "TAX", "name": "Taxable"},
                    "BillableStatus": "NotBillable",
                }
            )
        ]
    )
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(connector)

    posted = mock_client.update_purchase_raw.call_args[0][0]
    detail = posted["Line"][0]["ItemBasedExpenseLineDetail"]
    assert detail["ClassRef"] == {"value": "class-1", "name": "Phase 1"}
    assert detail["TaxCodeRef"] == {"value": "TAX", "name": "Taxable"}
    assert detail["BillableStatus"] == "NotBillable"
    assert detail["CustomerRef"] == {"value": FAKE_CUSTOMER_REF.value, "name": FAKE_CUSTOMER_REF.name}


def test_fail_closed_sync_token_mismatch_no_write():
    """Stale expected_sync_token on placeholder raises PurchaseChangedInQboError; no update."""
    fresh = _make_raw_purchase(sync_token="9", lines=[_make_categorize_line_dict()])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        with pytest.raises(PurchaseChangedInQboError) as exc_info:
            _call_recode(connector, expected_sync_token=SYNC_TOKEN)

    err = exc_info.value
    assert err.qbo_purchase_qbo_id == PURCHASE_QBO_ID
    assert err.expected_sync_token == SYNC_TOKEN
    assert err.actual_sync_token == "9"
    mock_client.update_purchase_raw.assert_not_called()


def test_foreign_recode_raises_no_write():
    """Target already recoded to a different item raises PurchaseChangedInQboError."""
    foreign_line = {
        "Id": TARGET_LINE_ID,
        "LineNum": 1,
        "Description": "done",
        "Amount": "10.00",
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": FOREIGN_ITEM_REF.value, "name": FOREIGN_ITEM_REF.name},
        },
    }
    fresh = _make_raw_purchase(sync_token="9", lines=[foreign_line])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        with pytest.raises(PurchaseChangedInQboError) as exc_info:
            _call_recode(connector, expected_sync_token=SYNC_TOKEN)

    err = exc_info.value
    assert err.qbo_purchase_qbo_id == PURCHASE_QBO_ID
    assert err.expected_sync_token == SYNC_TOKEN
    assert err.actual_sync_token == "9"
    mock_client.update_purchase_raw.assert_not_called()


def test_own_idempotent_retry_already_recoded():
    """Target already ItemBased with our item ref returns already_recoded; no write."""
    already_recoded = {
        "Id": TARGET_LINE_ID,
        "LineNum": 1,
        "Description": "done",
        "Amount": "10.00",
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": FAKE_ITEM_REF.value, "name": FAKE_ITEM_REF.name},
        },
    }
    fresh = _make_raw_purchase(lines=[already_recoded])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        result = _call_recode(connector)

    assert result == {"status": "already_recoded", "sync_token": SYNC_TOKEN}
    mock_client.update_purchase_raw.assert_not_called()


def test_race_on_update_surfaces_purchase_changed_in_qbo_error():
    """QboSyncTokenMismatchError on update becomes non-retryable PurchaseChangedInQboError."""
    fresh = _make_raw_purchase(lines=[_make_categorize_line_dict()])
    connector = _build_connector()

    mock_client = MagicMock()
    mock_client.get_purchase_raw.return_value = copy.deepcopy(fresh)
    mock_client.update_purchase_raw.side_effect = QboSyncTokenMismatchError("stale sync token")

    mock_client_class = MagicMock()
    mock_client_class.return_value.__enter__.return_value = mock_client
    mock_client_class.return_value.__exit__.return_value = None

    with patch(
        "integrations.intuit.qbo.purchase.external.client.QboPurchaseClient",
        mock_client_class,
    ):
        with pytest.raises(PurchaseChangedInQboError) as exc_info:
            _call_recode(connector)

    assert exc_info.value.expected_sync_token == SYNC_TOKEN
    assert exc_info.value.actual_sync_token == "unknown"
    assert not isinstance(exc_info.value, QboSyncTokenMismatchError)
    mock_client.update_purchase_raw.assert_called_once()


def test_missing_item_mapping_raises_no_write():
    """Missing SubCostCode→Item mapping fails closed; no write."""
    sibling = _make_item_sibling_dict()
    fresh = _make_raw_purchase(lines=[_make_categorize_line_dict(), sibling])
    connector = _build_connector(item_ref=None)

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        with pytest.raises(PurchaseRecodeMappingError) as exc_info:
            _call_recode(connector)

    assert exc_info.value.sub_cost_code_id == SUB_COST_CODE_ID
    mock_client.update_purchase_raw.assert_not_called()


def test_readonly_fields_stripped_unmodeled_header_preserved():
    """MetaData/domain/sparse are stripped before POST; an unmodeled header field survives."""
    fresh = _make_raw_purchase(
        lines=[_make_categorize_line_dict()],
        extra_header={
            "MetaData": {"CreateTime": "2026-07-01T00:00:00-05:00", "LastUpdatedTime": "x"},
            "domain": "QBO",
            "sparse": False,
            "TxnSource": "Ramp",  # unmodeled by our schema — must pass through untouched
        },
    )
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(connector)

    posted = mock_client.update_purchase_raw.call_args[0][0]
    # server-owned / read-only markers removed
    assert "MetaData" not in posted
    assert "domain" not in posted
    assert "sparse" not in posted
    # genuine unmodeled business field preserved verbatim
    assert posted["TxnSource"] == "Ramp"


def test_foreign_recode_matching_token_still_raises():
    """Line already off 58999 to a DIFFERENT item raises even when the SyncToken matches
    (proves the fail-closed path keys on the item mismatch, not only on token drift)."""
    foreign_line = {
        "Id": TARGET_LINE_ID,
        "LineNum": 1,
        "Description": "done",
        "Amount": "10.00",
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": FOREIGN_ITEM_REF.value, "name": FOREIGN_ITEM_REF.name},
        },
    }
    fresh = _make_raw_purchase(sync_token=SYNC_TOKEN, lines=[foreign_line])
    connector = _build_connector()

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        with pytest.raises(PurchaseChangedInQboError):
            _call_recode(connector, expected_sync_token=SYNC_TOKEN)

    mock_client.update_purchase_raw.assert_not_called()
