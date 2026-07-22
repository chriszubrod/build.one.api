"""Pure-logic tests for vendor compliance coverage resolution (no DB/network)."""
from datetime import date
from types import SimpleNamespace

from entities.vendor_compliance.business.coverage_resolver import resolve_coverage_map

TODAY = date(2026, 7, 21)


def _policy(coverage_type, *, expiry_date=None, **extra):
    defaults = {
        "carrier": None,
        "policy_number": None,
        "each_occurrence": None,
        "aggregate": None,
        "public_id": None,
        "certificate_public_id": None,
    }
    defaults.update(extra)
    return SimpleNamespace(
        coverage_type=coverage_type,
        expiry_date=expiry_date,
        **defaults,
    )


def test_both_required_valid():
    policies = [
        _policy("GL", expiry_date="2027-01-01"),
        _policy("WC", expiry_date="2027-06-01"),
    ]
    result = resolve_coverage_map(["GL", "WC"], policies, TODAY)

    assert result["compliant"] is True
    assert result["status"] == "valid"
    assert result["coverages"]["GL"]["status"] == "valid"
    assert result["coverages"]["WC"]["status"] == "valid"
    assert result["coverages"]["GL"]["required"] is True
    assert result["coverages"]["WC"]["required"] is True


def test_wc_expired():
    policies = [
        _policy("GL", expiry_date="2027-01-01"),
        _policy("WC", expiry_date="2026-02-24"),
    ]
    result = resolve_coverage_map(["GL", "WC"], policies, TODAY)

    assert result["compliant"] is False
    assert result["status"] == "expired"
    assert result["coverages"]["WC"]["status"] == "expired"


def test_gl_missing():
    policies = [_policy("WC", expiry_date="2027-06-01")]
    result = resolve_coverage_map(["GL", "WC"], policies, TODAY)

    assert result["coverages"]["GL"] == {
        "coverage_type": "GL",
        "required": True,
        "status": "missing",
    }
    assert result["status"] == "missing"
    assert result["compliant"] is False


def test_expiring_wc_still_compliant():
    policies = [
        _policy("GL", expiry_date="2027-01-01"),
        _policy("WC", expiry_date="2026-08-10"),
    ]
    result = resolve_coverage_map(["GL", "WC"], policies, TODAY)

    assert result["coverages"]["WC"]["status"] == "expiring"
    assert result["compliant"] is True
    assert result["status"] == "expiring"


def test_incomplete_when_policy_has_no_expiry():
    policies = [_policy("GL", expiry_date=None)]
    result = resolve_coverage_map(["GL"], policies, TODAY)

    assert result["coverages"]["GL"]["status"] == "incomplete"
    assert result["compliant"] is False


def test_empty_required_with_policy():
    policies = [_policy("GL", expiry_date="2027-01-01")]
    result = resolve_coverage_map([], policies, TODAY)

    assert result["compliant"] is True
    assert result["status"] == "valid"


def test_empty_required_no_policies():
    result = resolve_coverage_map([], [], TODAY)

    assert result["status"] == "missing"


def test_extra_coverages_not_in_coverages_map():
    policies = [
        _policy("GL", expiry_date="2027-01-01"),
        _policy("OTHER", expiry_date="2027-05-01"),
    ]
    result = resolve_coverage_map(["GL"], policies, TODAY)

    assert "OTHER" not in result["coverages"]
    assert len(result["extra_coverages"]) == 1
    assert result["extra_coverages"][0]["coverage_type"] == "OTHER"
    assert result["extra_coverages"][0]["required"] is False


def test_max_expiry_picks_best_gl_policy():
    policies = [
        _policy("GL", expiry_date="2026-01-01"),
        _policy("GL", expiry_date="2027-12-31"),
    ]
    result = resolve_coverage_map(["GL"], policies, TODAY)

    assert result["coverages"]["GL"]["status"] == "valid"
    assert result["coverages"]["GL"]["expiry_date"] == "2027-12-31"
