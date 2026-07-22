"""Pure-logic tests for contractors license field extraction from DI layout results."""
import pytest

from entities.contractors_license.business.cl_parser import parse_contractors_license_fields


def test_state_of_tennessee_board_for_licensing_contractors():
    di = {
        "content": (
            "State of Tennessee\n"
            "BOARD FOR LICENSING CONTRACTORS\n"
            "CONTRACTOR\n"
            "SCALES ELECTRIC, LLC\n"
            "ID NUMBER: 49103\n"
            "LIC STATUS: ACTIVE\n"
            "EXPIRATION DATE: July 31, 2027\n"
            "$240,000.00; CE"
        ),
        "key_value_pairs": [
            {"key": "ID NUMBER", "value": "49103"},
            {"key": "EXPIRATION DATE", "value": "July 31, 2027"},
        ],
    }
    result = parse_contractors_license_fields(di)

    assert result["license_number"] == "49103"
    assert result["expiry_date"] == "2027-07-31"
    assert result["classification"] == "CE"
    assert result["confidence"] == 1.0
    assert result["issue_date"] is None
    assert "issue_date" in result["unresolved"]
    auth = result["issuing_authority"] or ""
    assert "Tennessee" in auth or "Board" in auth


def test_metro_nashville_davidson_state_electrical_contractor():
    di = {
        "content": (
            "Metropolitan Government of Nashville & Davidson County\n"
            "BOARD OF MECHANICAL, PLUMBING, AND ELECTRICAL EXAMINERS\n"
            "Certificate No. DC328\n"
            "Receipt No. 2669115\n"
            "registered as a STATE ELECTRICAL CONTRACTOR\n"
            "EXPIRATION DATE 03/31/2027"
        ),
        "key_value_pairs": [
            {"key": "Certificate No.", "value": "DC328"},
            {"key": "EXPIRATION DATE", "value": "03/31/2027"},
        ],
    }
    result = parse_contractors_license_fields(di)

    assert result["license_number"] == "DC328"
    assert result["expiry_date"] == "2027-03-31"
    assert result["classification"] == "STATE ELECTRICAL CONTRACTOR"
    auth = result["issuing_authority"] or ""
    assert "Metropolitan" in auth or "Davidson" in auth


def test_sparse_empty_di():
    di = {"content": "", "key_value_pairs": []}
    result = parse_contractors_license_fields(di)

    assert result["license_number"] is None
    assert result["issuing_authority"] is None
    assert result["classification"] is None
    assert result["issue_date"] is None
    assert result["expiry_date"] is None
    assert result["confidence"] == 0.0
    assert set(result["unresolved"]) == {
        "license_number",
        "issuing_authority",
        "classification",
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
    result = parse_contractors_license_fields(payload)
    assert isinstance(result, dict)
    assert "confidence" in result
    assert isinstance(result["unresolved"], list)


@pytest.mark.parametrize("bad", ["99/99/2026", "13/45/2026", "not a date"])
def test_malformed_dates_do_not_recurse(bad):
    di = {
        "content": f"EXPIRATION DATE {bad}\nID NUMBER 49103",
        "key_value_pairs": [{"key": "EXPIRATION DATE", "value": bad}],
    }
    result = parse_contractors_license_fields(di)
    assert result["expiry_date"] is None
    assert result["license_number"] == "49103"
