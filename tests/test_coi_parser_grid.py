"""Grid-table extraction tests using real-derived ACORD DI fixtures."""
import json
import os

from entities.certificate_of_insurance.business.coi_parser import (
    parse_certificate_of_insurance_fields,
)


def _load_fixture(name: str) -> dict:
    path = os.path.join(os.path.dirname(__file__), "fixtures", name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_acord_gl_auto_di_grid_extracts_gl_and_auto_not_wc():
    di = _load_fixture("acord_gl_auto_di.json")
    result = parse_certificate_of_insurance_fields(di)

    gl = next(p for p in result["policies"] if p["coverage_type"] == "GL")
    assert gl["policy_number"] == "GLTEST0001"
    assert gl["effective_date"] == "2024-12-14"
    assert gl["expiry_date"] == "2025-12-14"
    assert gl["each_occurrence"] == "1000000"
    assert gl["carrier"] and "Alpha" in gl["carrier"]

    auto = next(p for p in result["policies"] if p["coverage_type"] == "OTHER")
    assert auto["policy_number"] == "AUTOTEST0002"
    assert auto["expiry_date"] == "2025-12-14"
    assert auto["carrier"] and "Beta" in auto["carrier"]

    assert not any(p["coverage_type"] == "WC" for p in result["policies"])
    assert result["confidence"] >= 0.5
    auth = result["issuing_authority"] or ""
    assert "Test Producer" in auth
    assert result["issue_date"] == "2025-04-23"


def test_acord_wc_di_grid_extracts_wc_only():
    di = _load_fixture("acord_wc_di.json")
    result = parse_certificate_of_insurance_fields(di)

    wc = next(p for p in result["policies"] if p["coverage_type"] == "WC")
    assert wc["policy_number"] == "WCTEST0003"
    assert wc["effective_date"] == "2025-02-24"
    assert wc["expiry_date"] == "2026-02-24"
    assert wc["carrier"] and "Gamma" in wc["carrier"]

    assert not any(p["coverage_type"] == "GL" for p in result["policies"])
    assert not any(p["coverage_type"] == "OTHER" for p in result["policies"])
