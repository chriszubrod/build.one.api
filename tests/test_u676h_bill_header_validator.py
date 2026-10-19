"""U-676h: bill header validator — new description fields and truncation."""
from __future__ import annotations

import pytest

from intelligence.cascade import bill_header_extraction as mod
from intelligence.cascade.bill_header_extraction import (
    MAX_LONG_DESCRIPTION_LEN,
    MAX_REFERENCE_LEN,
    MAX_SHORT_DESCRIPTION_LEN,
    validate_bill_header_result,
)


def _base(**overrides):
    d = {
        "vendor_name": "Acme",
        "bill_number": "1",
        "bill_date": "2026-01-01",
        "total_amount": "10",
        "short_description": "Biweekly mowing",
        "long_description": "Biweekly mowing, Oct 1-15",
        "reference": "PO 1123",
        "confidence": 0.9,
    }
    d.update(overrides)
    return d


def test_system_prompt_requests_short_long_reference():
    prompt = mod.BILL_HEADER_EXTRACTION_TASK.system_prompt
    assert "short_description" in prompt
    assert "long_description" in prompt
    assert "reference" in prompt
    assert "<=120 chars" not in prompt


def test_accepts_new_fields():
    ok, reason = validate_bill_header_result(_base())
    assert ok, reason


def test_legacy_description_maps_to_long_when_long_absent():
    data = _base(long_description=None, short_description=None)
    del data["long_description"]
    data["description"] = "Legacy summary text"
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert data["long_description"] == "Legacy summary text"


def test_truncates_overlong_short_at_word_boundary():
    words = "word " * 30
    data = _base(short_description=words.strip())
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert len(data["short_description"]) <= MAX_SHORT_DESCRIPTION_LEN
    assert not data["short_description"].endswith(" ")


def test_truncates_overlong_long_at_word_boundary():
    text = ("lorem " * 80).strip()
    data = _base(long_description=text)
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert len(data["long_description"]) <= MAX_LONG_DESCRIPTION_LEN


def test_overlong_reference_is_dropped_not_cut():
    """A PO / job number cut short is a wrong identifier: drop it, keep the rest of the header."""
    for text in ("PO-" + ("9" * 100), 10**70):
        data = _base(reference=text)
        ok, reason = validate_bill_header_result(data)
        assert ok, reason
        assert data["reference"] is None
        assert data["vendor_name"]  # header intact


def test_reference_at_the_cap_is_kept():
    text = "PO-" + ("9" * (MAX_REFERENCE_LEN - 3))
    data = _base(reference=text)
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert data["reference"] == text


def test_non_string_short_description_becomes_null_header_ok():
    data = _base(short_description=12)
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert data["short_description"] is None
    assert data["vendor_name"] == "Acme"


def test_reference_json_int_coerced_to_string():
    data = _base(reference=1123)
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert data["reference"] == "1123"


@pytest.mark.parametrize(
    "bad_ref",
    [[1, 2], {"po": 1}, 12.5, True, False],
    ids=["list", "dict", "float", "true", "false"],
)
def test_invalid_reference_null_rest_intact(bad_ref):
    data = _base(reference=bad_ref)
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert data["reference"] is None
    assert data["vendor_name"] == "Acme"
    assert data["total_amount"] == "10"


def test_whitespace_vendor_name_becomes_null():
    data = _base(vendor_name="   ")
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert data["vendor_name"] is None


def test_rejects_overlong_vendor_name_not_truncated():
    data = _base(vendor_name="A" + "b" * 300)
    ok, reason = validate_bill_header_result(data)
    assert not ok
    assert "vendor_name" in reason


def test_unexpected_key_rejected():
    ok, reason = validate_bill_header_result(_base(extra_field="nope"))
    assert not ok
    assert "unexpected keys" in reason
