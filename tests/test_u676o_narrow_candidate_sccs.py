"""U-676o: narrowed SubCostCode candidate list (measurement-only pure helpers)."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from intelligence.cascade.bill_coding_payload import (
    PriorBillVote,
    narrow_candidate_sccs,
    vendor_history_scc_numbers_from_priors,
)


def _full_catalog() -> list[dict[str, str]]:
    return [
        {"number": "43.00", "name": "A"},
        {"number": "43.10", "name": "B"},
        {"number": "43.20", "name": "C"},
        {"number": "65.10", "name": "D"},
        {"number": "65.20", "name": "E"},
        {"number": "99.01", "name": "F"},
    ]


@pytest.mark.parametrize(
    "history,min_history,expected_numbers,narrowed",
    [
        (["43.00"], 3, None, False),
        (["43.00", "65.10"], 3, None, False),
        (
            ["43.00", "65.10", "99.01"],
            3,
            ["43.00", "43.10", "43.20", "65.10", "65.20", "99.01"],
            True,
        ),
        (["43.00", "65.10", "88.00"], 3, None, False),
    ],
)
def test_narrow_candidate_sccs_table(history, min_history, expected_numbers, narrowed):
    full = _full_catalog()
    if expected_numbers is None:
        expected_numbers = [r["number"] for r in full]
    out, was_narrowed = narrow_candidate_sccs(
        full, history, min_history=min_history
    )
    assert [r["number"] for r in out] == expected_numbers
    assert was_narrowed is narrowed


def test_narrow_three_history_codes_none_present_in_full_returns_full():
    full = _full_catalog()
    out, narrowed = narrow_candidate_sccs(
        full, ["88.00", "77.00", "66.00"], min_history=3
    )
    assert narrowed is False
    assert out == full


def test_narrow_three_history_two_missing_from_full_returns_full():
    full = _full_catalog()
    out, narrowed = narrow_candidate_sccs(
        full, ["43.00", "88.00", "77.00"], min_history=3
    )
    assert narrowed is False
    assert out == full


def test_narrow_ignores_history_code_missing_from_full_list():
    full = _full_catalog()
    out, narrowed = narrow_candidate_sccs(
        full, ["43.00", "65.10", "DELETED.01"], min_history=3
    )
    assert narrowed is False
    assert out == full


def test_narrow_preserves_full_list_order():
    full = [
        {"number": "10.01", "name": "x"},
        {"number": "10.02", "name": "y"},
        {"number": "20.01", "name": "z"},
        {"number": "20.02", "name": "w"},
    ]
    out1, n1 = narrow_candidate_sccs(
        full, ["10.01", "20.01", "20.02"], min_history=3
    )
    out2, n2 = narrow_candidate_sccs(out1, ["10.01", "20.01", "20.02"], min_history=3)
    assert n1 and n2
    assert out1 == out2 == [
        {"number": "10.01", "name": "x"},
        {"number": "10.02", "name": "y"},
        {"number": "20.01", "name": "z"},
        {"number": "20.02", "name": "w"},
    ]


def test_vendor_history_from_priors_distinct():
    priors = [
        PriorBillVote(1, datetime(2026, 1, 1), 1, 10),
        PriorBillVote(2, datetime(2026, 1, 2), 1, 10),
        PriorBillVote(3, datetime(2026, 1, 3), 2, 11),
    ]
    scc_by_id = {
        1: SimpleNamespace(number="43.00", name="A"),
        2: SimpleNamespace(number="65.10", name="B"),
    }
    assert set(vendor_history_scc_numbers_from_priors(priors, scc_by_id)) == {
        "43.00",
        "65.10",
    }
