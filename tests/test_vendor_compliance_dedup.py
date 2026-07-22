"""Pure-logic tests for SharePoint folder-import compliance doc dedup (U-104)."""
from types import SimpleNamespace

from entities.vendor_compliance_document.business.folder_helpers import (
    select_duplicate_compliance_doc,
)

DOC_TYPE = "CERTIFICATE_OF_INSURANCE"
OTHER_TYPE = "CONTRACTORS_LICENSE"
HASH_A = "abc123"
HASH_B = "def456"


def _doc(label: str):
    return SimpleNamespace(label=label)


def test_matching_type_and_hash_returns_doc():
    target = _doc("match")
    candidates = [
        (OTHER_TYPE, HASH_A, _doc("wrong-type")),
        (DOC_TYPE, HASH_B, _doc("wrong-hash")),
        (DOC_TYPE, HASH_A, target),
    ]
    assert select_duplicate_compliance_doc(candidates, DOC_TYPE, HASH_A) is target


def test_same_type_different_hash_returns_none():
    candidates = [(DOC_TYPE, HASH_B, _doc("other"))]
    assert select_duplicate_compliance_doc(candidates, DOC_TYPE, HASH_A) is None


def test_different_type_same_hash_returns_none():
    candidates = [(OTHER_TYPE, HASH_A, _doc("other"))]
    assert select_duplicate_compliance_doc(candidates, DOC_TYPE, HASH_A) is None


def test_falsy_file_hash_returns_none():
    candidates = [(DOC_TYPE, HASH_A, _doc("x"))]
    assert select_duplicate_compliance_doc(candidates, DOC_TYPE, None) is None
    assert select_duplicate_compliance_doc(candidates, DOC_TYPE, "") is None


def test_empty_candidates_returns_none():
    assert select_duplicate_compliance_doc([], DOC_TYPE, HASH_A) is None


def test_multiple_matches_returns_first():
    first = _doc("first")
    second = _doc("second")
    candidates = [
        (DOC_TYPE, HASH_A, first),
        (DOC_TYPE, HASH_A, second),
    ]
    assert select_duplicate_compliance_doc(candidates, DOC_TYPE, HASH_A) is first
