"""U-186 — pure ReimburseCharge parse logic (durable RC staging)."""

from decimal import Decimal

from integrations.intuit.qbo.reimburse_charge.business.parse import parse_reimburse_charge


def _rc(*, rc_id="900", amount="1577.45", invoiced=False):
    return {
        "Id": rc_id,
        "CustomerRef": {"value": "77", "name": "Haverford"},
        "TxnDate": "2026-07-01",
        "Amount": amount,
        "HasBeenInvoiced": invoiced,
    }


def test_parse_basic_fields():
    out = parse_reimburse_charge(_rc())
    assert out["qbo_id"] == "900"
    assert out["customer_ref_value"] == "77"
    assert out["customer_ref_name"] == "Haverford"
    assert out["txn_date"] == "2026-07-01"
    assert out["has_been_invoiced"] is False


def test_parse_amount_is_decimal_not_float():
    out = parse_reimburse_charge(_rc(amount="1577.45"))
    assert out["amount"] == Decimal("1577.45")
    assert isinstance(out["amount"], Decimal)


def test_parse_invoiced_flag_true():
    out = parse_reimburse_charge(_rc(invoiced=True))
    assert out["has_been_invoiced"] is True


def test_parse_integer_id_coerced_to_string():
    out = parse_reimburse_charge(_rc(rc_id=900))
    assert out["qbo_id"] == "900"


def test_parse_empty_dict_returns_all_none():
    out = parse_reimburse_charge({})
    assert out["qbo_id"] is None
    assert out["customer_ref_value"] is None
    assert out["customer_ref_name"] is None
    assert out["txn_date"] is None
    assert out["amount"] is None
    assert out["has_been_invoiced"] is None
