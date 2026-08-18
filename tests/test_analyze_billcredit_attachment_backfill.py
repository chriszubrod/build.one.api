"""Pure-logic tests for analyze_billcredit_attachment_backfill helpers (U-261 investigate)."""
from __future__ import annotations

from types import SimpleNamespace

from scripts.analyze_billcredit_attachment_backfill import _ref_matches_vendor_credit


def _ref(entity_ref_type, entity_ref_value: str) -> SimpleNamespace:
    return SimpleNamespace(entity_ref_type=entity_ref_type, entity_ref_value=entity_ref_value)


def test_ref_matches_vendor_credit_matching_ref():
    ref = _ref("VendorCredit", "12345")
    assert _ref_matches_vendor_credit(ref, {"12345"}) == "12345"


def test_ref_matches_vendor_credit_case_insensitive_type():
    ref = _ref("vendorcredit", "12345")
    assert _ref_matches_vendor_credit(ref, {"12345"}) == "12345"


def test_ref_matches_vendor_credit_wrong_qbo_id():
    ref = _ref("VendorCredit", "12345")
    assert _ref_matches_vendor_credit(ref, {"99999"}) is None


def test_ref_matches_vendor_credit_non_vendor_credit_ref():
    ref = _ref("Bill", "12345")
    assert _ref_matches_vendor_credit(ref, {"12345"}) is None


def test_ref_matches_vendor_credit_none_type():
    ref = _ref(None, "12345")
    assert _ref_matches_vendor_credit(ref, {"12345"}) is None


def test_ref_matches_vendor_credit_matches_one_of_many_ids():
    ref = _ref("VendorCredit", "12345")
    assert _ref_matches_vendor_credit(ref, {"99999", "12345", "77777"}) == "12345"
