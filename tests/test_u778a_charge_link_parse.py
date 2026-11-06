"""U-778a bite 2: ReimburseCharge link extractor + line-schema LinkedTxn parsing."""
import logging
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.base.charge_link import reimburse_charge_id_from_linked_txn as extract
from integrations.intuit.qbo.bill.external.schemas import QboBillLine
from integrations.intuit.qbo.purchase.external.schemas import QboPurchaseLine
from integrations.intuit.qbo.vendorcredit.external.schemas import QboVendorCreditLine

LINE_JSON = {
    "Id": "1", "LineNum": 1, "Amount": 32.5, "DetailType": "AccountBasedExpenseLineDetail",
    "AccountBasedExpenseLineDetail": {
        "AccountRef": {"value": "113"}, "CustomerRef": {"value": "1383"},
        "BillableStatus": "HasBeenBilled", "MarkupInfo": {"Percent": 50},
    },
    "LinkedTxn": [{"TxnId": "68056", "TxnType": "ReimburseCharge"}],
}


def test_none_and_empty():
    assert extract(None) is None
    assert extract([]) is None


def test_one_dict():
    assert extract([{"TxnId": "68056", "TxnType": "ReimburseCharge"}]) == "68056"


def test_strips_and_stringifies():
    assert extract([{"TxnId": " 68056 ", "TxnType": "ReimburseCharge"}]) == "68056"
    assert extract([{"TxnId": 68056, "TxnType": "ReimburseCharge"}]) == "68056"


def test_other_types_ignored():
    assert extract([{"TxnId": "5", "TxnType": "Invoice"}]) is None
    assert extract([{"TxnId": "5", "TxnType": "Invoice"}, {"TxnId": "9", "TxnType": "ReimburseCharge"}]) == "9"


def test_same_id_twice():
    assert extract([{"TxnId": "7", "TxnType": "ReimburseCharge"}, {"TxnId": "7", "TxnType": "ReimburseCharge"}]) == "7"


def test_two_distinct_ids_none_and_warning(caplog):
    with caplog.at_level(logging.WARNING):
        got = extract([{"TxnId": "7", "TxnType": "ReimburseCharge"}, {"TxnId": "8", "TxnType": "ReimburseCharge"}])
    assert got is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_pydantic_like_objects():
    assert extract([SimpleNamespace(txn_id="68056", txn_type="ReimburseCharge")]) == "68056"
    assert extract([SimpleNamespace(txn_id="1", txn_type="Bill")]) is None


def test_blank_id_ignored():
    assert extract([{"TxnId": "  ", "TxnType": "ReimburseCharge"}, {"TxnType": "ReimburseCharge"}]) is None


@pytest.mark.parametrize("model", [QboBillLine, QboPurchaseLine, QboVendorCreditLine])
def test_line_schema_parses_linked_txn(model):
    line = model(**LINE_JSON)
    assert extract(line.linked_txn) == "68056"
    assert line.linked_txn[0].txn_type == "ReimburseCharge"


@pytest.mark.parametrize("model", [QboBillLine, QboPurchaseLine, QboVendorCreditLine])
def test_line_schema_without_linked_txn(model):
    d = {k: v for k, v in LINE_JSON.items() if k != "LinkedTxn"}
    assert extract(model(**d).linked_txn) is None


@pytest.mark.parametrize("model", [QboBillLine, QboPurchaseLine, QboVendorCreditLine])
def test_outbound_payload_never_emits_line_linked_txn(model):
    payload = model(**LINE_JSON).model_dump(by_alias=True, exclude_none=True, mode="json")
    assert "LinkedTxn" not in payload
    assert payload["Id"] == "1"


@pytest.mark.parametrize("model", [QboBillLine, QboPurchaseLine, QboVendorCreditLine])
def test_f3_integer_txn_id_does_not_abort_parsing(model):
    d = dict(LINE_JSON, LinkedTxn=[{"TxnId": 68056, "TxnType": "ReimburseCharge"}])
    line = model(**d)
    assert extract(line.linked_txn) == "68056"
    assert line.linked_txn[0].txn_id == "68056"


@pytest.mark.parametrize("model", [QboBillLine, QboPurchaseLine, QboVendorCreditLine])
def test_f3_null_txn_id_stays_none(model):
    d = dict(LINE_JSON, LinkedTxn=[{"TxnId": None, "TxnType": "ReimburseCharge"}])
    assert model(**d).linked_txn[0].txn_id is None


# --- U-778a fix round 2 (reviewer P2): a malformed TxnId must never become a link -----------------
@pytest.mark.parametrize("model", [QboBillLine, QboPurchaseLine, QboVendorCreditLine])
@pytest.mark.parametrize("bad", [True, False, {}, [], 1.5, {"x": 1}])
def test_r2_malformed_txn_id_is_no_link_not_a_string(model, bad):
    d = dict(LINE_JSON, LinkedTxn=[{"TxnId": bad, "TxnType": "ReimburseCharge"}])
    line = model(**d)  # must not raise: a malformed id cannot abort a pull page
    assert line.linked_txn[0].txn_id is None
    assert extract(line.linked_txn) is None


@pytest.mark.parametrize("bad", [True, {}, [], 1.5])
def test_r2_extractor_rejects_malformed_dict_ids(bad):
    assert extract([{"TxnId": bad, "TxnType": "ReimburseCharge"}]) is None


@pytest.mark.parametrize("model", [QboBillLine, QboPurchaseLine, QboVendorCreditLine])
def test_r2_non_string_txn_type_never_matches(model):
    d = dict(LINE_JSON, LinkedTxn=[{"TxnId": "68056", "TxnType": ["ReimburseCharge"]}])
    assert extract(model(**d).linked_txn) is None


# --- U-778a fix round 3 (reviewer P2): a QBO id is a digit string; a malformed STRING is no link either ---
@pytest.mark.parametrize("model", [QboBillLine, QboPurchaseLine, QboVendorCreditLine])
@pytest.mark.parametrize("bad", ["True", "{}", "[]", "1.5", "-5", "12a", "6 8056", "abc"])
def test_r3_malformed_string_txn_id_is_no_link(model, bad):
    d = dict(LINE_JSON, LinkedTxn=[{"TxnId": bad, "TxnType": "ReimburseCharge"}])
    line = model(**d)
    assert line.linked_txn[0].txn_id is None
    assert extract(line.linked_txn) is None


@pytest.mark.parametrize("good,expected", [("68056", "68056"), (" 68056 ", "68056"), (68056, "68056"), ("0", "0")])
def test_r3_valid_ids_still_accepted(good, expected):
    assert extract([{"TxnId": good, "TxnType": "ReimburseCharge"}]) == expected


def test_r3_negative_int_id_is_no_link():
    assert extract([{"TxnId": -5, "TxnType": "ReimburseCharge"}]) is None


# --- U-778a fix round 4 (reviewer P2): never an id longer than the NVARCHAR(50) column (it would truncate) ---
@pytest.mark.parametrize("bad", ["9" * 51, 10 ** 50, 10 ** 5000], ids=["str51", "int1e50", "int1e5000"])
def test_r4_oversized_id_is_no_link_and_never_raises(bad):
    assert extract([{"TxnId": bad, "TxnType": "ReimburseCharge"}]) is None


@pytest.mark.parametrize("model", [QboBillLine, QboPurchaseLine, QboVendorCreditLine])
def test_r4_oversized_id_through_schemas(model):
    d = dict(LINE_JSON, LinkedTxn=[{"TxnId": "9" * 51, "TxnType": "ReimburseCharge"}])
    assert model(**d).linked_txn[0].txn_id is None


@pytest.mark.parametrize("good", ["9" * 50, 10 ** 49], ids=["str50", "int1e49"])
def test_r4_ids_at_the_column_width_are_kept(good):
    assert extract([{"TxnId": good, "TxnType": "ReimburseCharge"}]) == str(good)
