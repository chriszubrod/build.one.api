"""U-676f: bill header validator numeric coercions."""
from __future__ import annotations

import math

import pytest

from intelligence.cascade.bill_header_extraction import validate_bill_header_result


def _base(**overrides):
    d = {
        "vendor_name": "Acme",
        "bill_number": "INV-1",
        "bill_date": "2026-01-15",
        "total_amount": "100.00",
        "description": "parts",
        "confidence": 0.9,
    }
    d.update(overrides)
    return d


def test_string_paths_unchanged():
    ok, _ = validate_bill_header_result(_base())
    assert ok


def test_total_amount_json_number_accepted():
    ok, reason = validate_bill_header_result(_base(total_amount=123.45))
    assert ok, reason


def test_total_amount_rejects_bool():
    ok, reason = validate_bill_header_result(_base(total_amount=True))
    assert not ok
    assert "total_amount" in reason


def test_total_amount_rejects_negative_number():
    ok, _ = validate_bill_header_result(_base(total_amount=-1))
    assert not ok


def test_total_amount_rejects_nan():
    ok, _ = validate_bill_header_result(_base(total_amount=math.nan))
    assert not ok


def test_bill_number_json_number_to_string():
    data = _base(bill_number=10042)
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert data["bill_number"] == "10042"
    assert isinstance(data["bill_number"], str)


def test_bill_number_rejects_float():
    ok, _ = validate_bill_header_result(_base(bill_number=12.5))
    assert not ok


def test_bill_number_rejects_negative_int():
    ok, _ = validate_bill_header_result(_base(bill_number=-1))
    assert not ok


def test_total_amount_coerced_to_decimal_string():
    data = _base(total_amount=123.45)
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert data["total_amount"] == "123.45"
    assert isinstance(data["total_amount"], str)


def test_total_amount_rejects_huge():
    ok, _ = validate_bill_header_result(_base(total_amount=1e10))
    assert not ok


def test_total_amount_scientific_notation_coerced_to_fixed_point():
    data = _base(total_amount=1e3)
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert data["total_amount"] == "1000"
    assert "e" not in data["total_amount"].lower()

    data2 = _base(total_amount=1e-7)
    ok2, reason2 = validate_bill_header_result(data2)
    assert ok2, reason2
    assert data2["total_amount"] == "0.0000001"


def test_bill_number_rejects_bool():
    ok, reason = validate_bill_header_result(_base(bill_number=False))
    assert not ok
    assert "bill_number" in reason
