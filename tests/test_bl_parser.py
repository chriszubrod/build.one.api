"""Pure-logic tests for business license field extraction from DI layout results."""
import pytest

from entities.business_license.business.bl_parser import parse_business_license_fields


def test_city_style_tn_business_tax_license():
    di = {
        "content": (
            "City of Spring Hill\n"
            "Business Tax Standard License\n"
            "Letter ID: L1687455296\n"
            "Expiration Date: 15-May-2024\n"
            "Date Issued: 19-Sep-2023\n"
            "License Number: 1000432371"
        ),
        "key_value_pairs": [
            {"key": "License Number", "value": "1000432371"},
            {"key": "Expiration Date", "value": "15-May-2024"},
            {"key": "Date Issued", "value": "19-Sep-2023"},
        ],
    }
    result = parse_business_license_fields(di)

    assert result["license_number"] == "1000432371"
    assert result["issuing_authority"] == "City of Spring Hill"
    assert result["issue_date"] == "2023-09-19"
    assert result["expiry_date"] == "2024-05-15"
    assert result["confidence"] == 1.0
    assert result["unresolved"] == []


def test_county_style_nashville_davidson():
    di = {
        "content": (
            "NASHVILLE AND DAVIDSON COUNTY BUSINESS TAX LICENSE\n"
            "Davidson County Clerk's Office\n"
            "BUSINESS NUMBER 232493\n"
            "THIS LICENSE EXPIRES 05/15/2026\n"
            "ISSUE DATE 08/08/2025"
        ),
        "key_value_pairs": [
            {"key": "BUSINESS NUMBER", "value": "232493"},
            {"key": "THIS LICENSE EXPIRES", "value": "05/15/2026"},
            {"key": "ISSUE DATE", "value": "08/08/2025"},
        ],
    }
    result = parse_business_license_fields(di)

    assert result["license_number"] == "232493"
    assert result["issuing_authority"] is not None
    assert "Davidson County" in result["issuing_authority"]
    assert result["issue_date"] == "2025-08-08"
    assert result["expiry_date"] == "2026-05-15"


def test_sparse_empty_di():
    di = {"content": "", "key_value_pairs": []}
    result = parse_business_license_fields(di)

    assert result["license_number"] is None
    assert result["issuing_authority"] is None
    assert result["issue_date"] is None
    assert result["expiry_date"] is None
    assert result["confidence"] == 0.0
    assert set(result["unresolved"]) == {
        "license_number",
        "issuing_authority",
        "issue_date",
        "expiry_date",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": None, "key_value_pairs": None},
    ],
)
def test_robustness_missing_keys_do_not_raise(payload):
    result = parse_business_license_fields(payload)
    assert isinstance(result, dict)
    assert "confidence" in result
    assert isinstance(result["unresolved"], list)


@pytest.mark.parametrize("bad", ["99/99/2026", "13/45/2026", "00-Xyz-0000", "not a date"])
def test_malformed_dates_do_not_recurse(bad):
    # Regression (U-112 Codex P2): a date-looking-but-invalid value near a label
    # must not infinitely self-recurse in _parse_date. If it did, this would blow
    # the recursion limit and raise instead of returning cleanly.
    di = {
        "content": f"THIS LICENSE EXPIRES {bad}\nBUSINESS NUMBER 232493",
        "key_value_pairs": [{"key": "THIS LICENSE EXPIRES", "value": bad}],
    }
    result = parse_business_license_fields(di)
    assert result["expiry_date"] is None
    assert result["license_number"] == "232493"
