"""Pure-logic tests for certificate of insurance field extraction from DI layout results."""
import pytest

from entities.certificate_of_insurance.business.coi_parser import (
    parse_certificate_of_insurance_fields,
)


def test_acord_content_extracts_gl_wc_metadata():
    di = {
        "content": (
            "ACORD 25 Certificate of Liability Insurance\n"
            "COMMERCIAL GENERAL LIABILITY\n"
            "Policy GL-12345 POLICY EXP 01/01/2027\n"
            "EACH OCCURRENCE $1,000,000\n"
            "GENERAL AGGREGATE $2,000,000\n"
            "WORKERS COMPENSATION\n"
            "Policy WC-98765 POLICY EXP 02/24/2027\n"
            "DATE (MM/DD/YYYY) 07/01/2026\n"
            "PRODUCER: Acme Agency\n"
        ),
        "key_value_pairs": [
            {"key": "DATE (MM/DD/YYYY)", "value": "07/01/2026"},
            {"key": "PRODUCER", "value": "Acme Agency"},
        ],
    }
    result = parse_certificate_of_insurance_fields(di)

    gl = next(p for p in result["policies"] if p["coverage_type"] == "GL")
    wc = next(p for p in result["policies"] if p["coverage_type"] == "WC")

    assert gl["policy_number"] == "GL-12345"
    assert gl["expiry_date"] == "2027-01-01"
    assert gl["each_occurrence"] == "1000000"
    assert gl["aggregate"] == "2000000"
    assert wc["expiry_date"] == "2027-02-24"
    assert result["confidence"] == 1.0
    assert result["issue_date"] == "2026-07-01"
    auth = result["issuing_authority"] or ""
    assert "Acme" in auth


@pytest.mark.parametrize(
    "line,expected_type",
    [
        ("AUTOMOBILE LIABILITY policy ABC-1 POLICY EXP 01/01/2028", "OTHER"),
        ("WORKERS COMPENSATION policy WC-1 POLICY EXP 01/01/2028", "WC"),
        ("COMMERCIAL GENERAL LIABILITY policy GL-1 POLICY EXP 01/01/2028", "GL"),
    ],
)
def test_coverage_type_mapping(line, expected_type):
    di = {"content": line, "key_value_pairs": []}
    result = parse_certificate_of_insurance_fields(di)
    assert len(result["policies"]) == 1
    assert result["policies"][0]["coverage_type"] == expected_type


def test_malformed_date_does_not_raise_and_zero_confidence():
    di = {
        "content": "GENERAL LIABILITY policy exp 99/99/2026\n",
        "key_value_pairs": [],
    }
    result = parse_certificate_of_insurance_fields(di)

    gl = next((p for p in result["policies"] if p["coverage_type"] == "GL"), None)
    assert gl is not None
    assert gl["expiry_date"] is None
    assert result["confidence"] == 0.0


def test_policy_number_not_literal_policy_word():
    di = {
        "content": "COMMERCIAL GENERAL LIABILITY\nPOLICY\n",
        "key_value_pairs": [],
    }
    result = parse_certificate_of_insurance_fields(di)
    gl = next(p for p in result["policies"] if p["coverage_type"] == "GL")
    pn = gl["policy_number"]
    assert pn is None or (pn != "POLICY" and any(c.isdigit() for c in str(pn)))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": None, "key_value_pairs": None, "tables": None},
        None,
    ],
)
def test_sparse_empty_do_not_raise(payload):
    result = parse_certificate_of_insurance_fields(payload)
    assert result["policies"] == []
    assert result["confidence"] == 0.0


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("01/Jan/2027", "2027-01-01"),  # Codex #4: slash month-abbrev
        ("01-Jan-2027", "2027-01-01"),  # hyphen month-abbrev still parses
        ("07/01/2026", "2026-07-01"),   # numeric MM/DD/YYYY still parses
        ("99/99/2026", None),           # malformed -> None, no hang
    ],
)
def test_parse_date_handles_month_abbrev_separators(raw, expected):
    from entities.certificate_of_insurance.business.coi_parser import _parse_date

    assert _parse_date(raw) == expected
