"""U-728: bill memo composition parity with build.one.web billMemo.ts."""
from __future__ import annotations

import pytest

from entities.bill.business.bill_memo import compose_bill_memo, entered_by_display_name


@pytest.mark.parametrize(
    "params,want",
    [
        (
            {
                "entered_by": "Chris Z",
                "method": "PDF upload",
                "long_description": "Biweekly mowing service, Oct 1-15",
                "reference": "PO 1123",
                "read_bill_number": "INV-42",
                "current_bill_number": "INV-42",
            },
            "Chris Z via PDF upload | Biweekly mowing service, Oct 1-15 | Ref: PO 1123",
        ),
        (
            {
                "entered_by": "Chris Z",
                "method": "PDF drop",
                "long_description": None,
                "reference": None,
                "read_bill_number": None,
                "current_bill_number": "",
            },
            "Chris Z via PDF drop",
        ),
        (
            {
                "entered_by": "",
                "method": "Manual",
                "long_description": "Summary only",
                "reference": None,
                "read_bill_number": None,
                "current_bill_number": "",
            },
            "Manual | Summary only",
        ),
        (
            {
                "entered_by": "U",
                "method": "PDF upload",
                "long_description": None,
                "reference": "Job 2026-044",
                "read_bill_number": "A",
                "current_bill_number": "A",
            },
            "U via PDF upload | Ref: Job 2026-044",
        ),
        (
            {
                "entered_by": "U",
                "method": "PDF drop",
                "long_description": None,
                "reference": None,
                "read_bill_number": "INV-ORIG",
                "current_bill_number": "EDITED",
            },
            "U via PDF drop | DOC#: INV-ORIG",
        ),
        (
            {
                "entered_by": "U",
                "method": "PDF upload",
                "long_description": "x",
                "reference": None,
                "read_bill_number": "INV-42",
                "current_bill_number": " INV-42 ",
            },
            "U via PDF upload | x",
        ),
        (
            {
                "entered_by": "A",
                "method": "PDF drop",
                "long_description": None,
                "reference": None,
                "read_bill_number": None,
                "current_bill_number": "",
            },
            "A via PDF drop",
        ),
    ],
    ids=[
        "full format",
        "omits empty tail segments",
        "enteredBy empty uses method alone",
        "reference without long description",
        "DOC# when read differs from form",
        "no DOC# when read matches form",
        "method PDF drop",
    ],
)
def test_compose_bill_memo_parity(params, want):
    assert compose_bill_memo(**params) == want


def test_compose_bill_memo_excludes_unrelated_fields():
    memo = compose_bill_memo(
        entered_by="Chris Z",
        method="PDF upload",
        long_description="Long only",
        reference="R1",
        read_bill_number="N1",
        current_bill_number="N2",
    )
    assert memo == "Chris Z via PDF upload | Long only | Ref: R1 | DOC#: N1"
    assert "P1" not in memo
    assert "01-100" not in memo


@pytest.mark.parametrize(
    "firstname,lastname,username,want",
    [
        ("Chris", "Z", "cz", "Chris Z"),
        ("Chris", None, "cz", "Chris"),
        (None, "Z", "cz", "Z"),
        ("  ", "Z", "cz", "Z"),
        (None, None, "login", "login"),
        (None, None, None, ""),
        ("", "", "", ""),
    ],
)
def test_entered_by_display_name(firstname, lastname, username, want):
    assert entered_by_display_name(firstname, lastname, username) == want
