"""U-186 — pure ReimburseCharge parse/merge logic (durable RC staging)."""

from decimal import Decimal

from integrations.intuit.qbo.reimburse_charge.business.parse import (
    merge_reimburse_charge,
    parse_reimburse_charge,
)


def _rc(linked_txn, *, rc_id="900", amount="1577.45", invoiced=False):
    return {
        "Id": rc_id,
        "CustomerRef": {"value": "77", "name": "Haverford"},
        "TxnDate": "2026-07-01",
        "Amount": amount,
        "HasBeenInvoiced": invoiced,
        "LinkedTxn": linked_txn,
    }


def test_parse_linked_txn_as_list_bill_source():
    raw = _rc([{"TxnId": "555", "TxnType": "Bill", "TxnLineId": "3"}])
    out = parse_reimburse_charge(raw)
    assert out["qbo_id"] == "900"
    assert out["source_txn_type"] == "Bill"
    assert out["source_txn_id"] == "555"
    assert out["source_txn_line_id"] == "3"
    assert out["customer_ref_value"] == "77"
    assert out["customer_ref_name"] == "Haverford"
    assert out["has_been_invoiced"] is False


def test_parse_linked_txn_as_dict_purchase_source():
    # QBO collapses a single LinkedTxn to an object rather than a list.
    raw = _rc({"TxnId": "808", "TxnType": "Purchase", "TxnLineId": "1"})
    out = parse_reimburse_charge(raw)
    assert out["source_txn_type"] == "Purchase"
    assert out["source_txn_id"] == "808"
    assert out["source_txn_line_id"] == "1"


def test_parse_amount_is_decimal_not_float():
    out = parse_reimburse_charge(_rc([], amount="1577.45"))
    assert out["amount"] == Decimal("1577.45")
    assert isinstance(out["amount"], Decimal)


def test_parse_invoiced_dropped_linked_txn_yields_none_source():
    # Once HasBeenInvoiced=true, QBO drops the reverse LinkedTxn (KI-32).
    raw = _rc([], invoiced=True)
    out = parse_reimburse_charge(raw)
    assert out["has_been_invoiced"] is True
    assert out["source_txn_type"] is None
    assert out["source_txn_id"] is None
    assert out["source_txn_line_id"] is None


def test_parse_ignores_non_source_linked_txn_types():
    # An Invoice forward-link is not a source pointer.
    raw = _rc([{"TxnId": "42", "TxnType": "Invoice"}])
    out = parse_reimburse_charge(raw)
    assert out["source_txn_id"] is None
    assert out["source_txn_type"] is None


def test_parse_picks_first_bill_or_purchase_entry():
    raw = _rc([
        {"TxnId": "42", "TxnType": "Invoice"},
        {"TxnId": "555", "TxnType": "Bill", "TxnLineId": "7"},
    ])
    out = parse_reimburse_charge(raw)
    assert out["source_txn_type"] == "Bill"
    assert out["source_txn_id"] == "555"
    assert out["source_txn_line_id"] == "7"


def test_parse_missing_txn_line_id_is_none():
    raw = _rc([{"TxnId": "555", "TxnType": "Bill"}])
    out = parse_reimburse_charge(raw)
    assert out["source_txn_id"] == "555"
    assert out["source_txn_line_id"] is None


def test_parse_integer_ids_coerced_to_string():
    raw = _rc([{"TxnId": 555, "TxnType": "Bill", "TxnLineId": 3}], rc_id=900)
    out = parse_reimburse_charge(raw)
    assert out["qbo_id"] == "900"
    assert out["source_txn_id"] == "555"
    assert out["source_txn_line_id"] == "3"


def test_merge_preserves_prior_source_on_invoiced_flip():
    # Captured while un-invoiced...
    stored = parse_reimburse_charge(_rc([{"TxnId": "555", "TxnType": "Bill", "TxnLineId": "3"}]))
    # ...re-pulled after the flip with the reverse LinkedTxn dropped.
    incoming = parse_reimburse_charge(_rc([], invoiced=True))
    merged = merge_reimburse_charge(stored, incoming)
    # Source pointer preserved from the pre-flip capture.
    assert merged["source_txn_type"] == "Bill"
    assert merged["source_txn_id"] == "555"
    assert merged["source_txn_line_id"] == "3"
    # Non-source fields take the fresh value.
    assert merged["has_been_invoiced"] is True


def test_merge_incoming_source_overrides_stored():
    stored = {"source_txn_type": "Bill", "source_txn_id": "111", "source_txn_line_id": "1"}
    incoming = parse_reimburse_charge(_rc([{"TxnId": "222", "TxnType": "Purchase", "TxnLineId": "2"}]))
    merged = merge_reimburse_charge(stored, incoming)
    assert merged["source_txn_type"] == "Purchase"
    assert merged["source_txn_id"] == "222"
    assert merged["source_txn_line_id"] == "2"


def test_merge_both_null_stays_null():
    stored = {"source_txn_type": None, "source_txn_id": None, "source_txn_line_id": None}
    incoming = parse_reimburse_charge(_rc([], invoiced=True))
    merged = merge_reimburse_charge(stored, incoming)
    assert merged["source_txn_id"] is None
    assert merged["source_txn_type"] is None


def test_merge_partial_preserve_line_id_only():
    # A newer capture that lost only the line id keeps the stored line id but
    # takes the fresh txn id/type.
    stored = {"source_txn_type": "Bill", "source_txn_id": "111", "source_txn_line_id": "9"}
    incoming = {
        "qbo_id": "900",
        "source_txn_type": "Bill",
        "source_txn_id": "111",
        "source_txn_line_id": None,
    }
    merged = merge_reimburse_charge(stored, incoming)
    assert merged["source_txn_line_id"] == "9"
