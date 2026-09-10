"""
Basic TOC prints ONE ROW PER BILLABLE LINE ITEM — never a parent-level roll-up.

Regression pin for the BR-MAIN-29 finding (2026-09-10). `_build_toc_basic_pdf`
used to run rows through a `_consolidate_basic_toc_rows` helper that merged every
line sharing a source bill into a single summed row labelled "Multiple See Image".
For a bill split across cost codes that sum equals the bill's own TotalAmount, so
the customer-facing page showed an ENTITY TOTAL where a billed line was meant:
Proctor `3953` printed $270,298.00 and Siteworks `26-0183` printed $237,776.00 —
both exactly their bills' totals.

The grand total was never wrong (the merge summed the same lines), which is why
every money reconciliation passed while the presentation was wrong. These tests
therefore pin the ROW-LEVEL rendering, not just the total — a test that only
checked the total would have stayed green through the entire defect.
"""
import re

import pytest

pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")

from entities.invoice.api.router import _build_toc_basic_pdf


def _text_of(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(__import__("io").BytesIO(pdf_bytes))
    return re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in reader.pages))


def _row(**kw):
    base = {
        "source_type": "BillLineItem",
        "source_date": "08-26-2026",
        "vendor_name": "Proctor Marble and Granite LLC",
        "parent_number": "3953",
        "sub_cost_code_name": "Countertops",
        "billed_price": 170298.00,
        "attachment_public_id": "att-1",
    }
    base.update(kw)
    return base


def test_two_lines_on_one_bill_render_as_two_rows_not_a_summed_roll_up():
    """The exact BR-MAIN-29 shape: one bill, two cost codes, two billed lines."""
    # A third, unrelated line is included deliberately so the pair's roll-up
    # ($270,298.00) is NOT equal to the page's grand total ($276,886.75).
    # Without it the Total legitimately prints $270,298.00 and the assertion
    # below could not tell a consolidated ROW from the Total.
    rows = [
        _row(sub_cost_code_name="Tile Labor", billed_price=100000.00),
        _row(sub_cost_code_name="Countertops", billed_price=170298.00),
        _row(vendor_name="Clark Crane, LLC", parent_number="NV-13543",
             sub_cost_code_name="Miscellaneous", billed_price=6588.75,
             attachment_public_id="att-2"),
    ]
    text = _text_of(_build_toc_basic_pdf(rows))

    # Each billable line appears at its own amount ...
    assert "$100,000.00" in text
    assert "$170,298.00" in text
    # ... and their parent-level sum ($270,298.00 = the Bill's TotalAmount) is
    # NOT printed as a line. This is the assertion the old code failed.
    assert "$270,298.00" not in text
    # The consolidation placeholder must never reappear.
    assert "Multiple See Image" not in text
    # Both cost codes are named, rather than collapsed to one label.
    assert "Tile Labor" in text
    assert "Countertops" in text
    # ... and the grand total is unaffected by the split.
    assert "Total $276,886.75" in text


def test_grand_total_still_sums_every_billable_line():
    """Splitting rows must not change the money — the total is the same either way."""
    rows = [
        _row(sub_cost_code_name="Tile Labor", billed_price=100000.00),
        _row(sub_cost_code_name="Countertops", billed_price=170298.00),
        _row(vendor_name="Clark Crane, LLC", parent_number="NV-13543",
             sub_cost_code_name="Miscellaneous", billed_price=6588.75,
             attachment_public_id="att-2"),
    ]
    text = _text_of(_build_toc_basic_pdf(rows))
    assert "Total $276,886.75" in text  # 100000.00 + 170298.00 + 6588.75


def test_partially_billed_bill_shows_only_the_billed_line():
    """
    Clark Crane NV-13543: bill total $6,856.33, but its $267.58 'Processing Fee
    DO NOT BILL' line is not on the invoice, so it is not among `rows` at all.
    The page must show the billed line only and never the bill's own total.
    """
    rows = [_row(vendor_name="Clark Crane, LLC", parent_number="NV-13543",
                 sub_cost_code_name="Miscellaneous", billed_price=6588.75)]
    text = _text_of(_build_toc_basic_pdf(rows))
    assert "$6,588.75" in text
    assert "$6,856.33" not in text
    assert "$267.58" not in text


def test_row_count_matches_line_count_for_many_lines_on_one_parent():
    """Three lines on one bill => three rows, not one."""
    rows = [
        _row(sub_cost_code_name="A", billed_price=1.00),
        _row(sub_cost_code_name="B", billed_price=2.00),
        _row(sub_cost_code_name="C", billed_price=4.00),
    ]
    text = _text_of(_build_toc_basic_pdf(rows))
    for amt in ("$1.00", "$2.00", "$4.00"):
        assert amt in text
    assert "$7.00" not in text.replace("Total $7.00", "")  # only the Total may be 7
    assert "Total $7.00" in text
