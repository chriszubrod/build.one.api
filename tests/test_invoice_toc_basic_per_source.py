"""
Basic TOC prints ONE ROW PER SOURCE ENTITY; the expanded TOC is the per-line view.

Chris, 2026-11-03 (HP2-14): Brayan Salina `2026.09.30.HP2` printed fifteen rows,
one per sub cost code, where the summary page wants one row at $2,109.38
labelled "Multiple See Image". This reverses e56a5da4 (per-line rows, after
BR-MAIN-29) — but keeps its real guard: a row's amount is the sum of the lines
BILLED ON THIS INVOICE, never the bill's own TotalAmount. Rows are pinned at
row level, not just the total, because the total is the same either way.
"""
import re

import pytest

pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")

from entities.invoice.api.router import _build_toc_basic_pdf, _consolidate_basic_toc_rows


def _text_of(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(__import__("io").BytesIO(pdf_bytes))
    return re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in reader.pages))


def _row(**kw):
    base = {
        "source_type": "BillLineItem",
        "source_date": "09-30-2026",
        "vendor_name": "Brayan Salina",
        "parent_number": "2026.09.30.HP2",
        "parent_public_id": "bill-brayan",
        "sub_cost_code_name": "Framing Labor",
        "billed_price": 100.00,
        "attachment_public_id": "att-1",
    }
    base.update(kw)
    return base


def test_multi_scc_bill_renders_one_row_at_its_billed_sum():
    rows = [
        _row(sub_cost_code_name="Framing Labor", billed_price=1000.00),
        _row(sub_cost_code_name="Drywall Labor", billed_price=809.38),
        _row(sub_cost_code_name="Trim Labor", billed_price=300.00),
        # Unrelated bill so the pair's sum is not also the page total.
        _row(vendor_name="White Cap", parent_number="WC-1", parent_public_id="bill-wc",
             sub_cost_code_name="Lumber", billed_price=50.00, attachment_public_id="att-2"),
    ]
    out = _consolidate_basic_toc_rows(rows)
    assert len(out) == 2
    text = _text_of(_build_toc_basic_pdf(rows))
    assert "Multiple See Image" in text
    assert "$2,109.38" in text
    for line_amt in ("$1,000.00", "$809.38", "$300.00"):
        assert line_amt not in text
    assert "Total $2,159.38" in text


def test_lines_sharing_one_scc_keep_that_name():
    rows = [_row(billed_price=1.00), _row(billed_price=2.00)]
    (only,) = _consolidate_basic_toc_rows(rows)
    assert only["sub_cost_code_name"] == "Framing Labor"
    assert "Multiple See Image" not in _text_of(_build_toc_basic_pdf(rows))


def test_partially_billed_bill_shows_only_the_billed_lines():
    """Clark Crane NV-13543: bill total $6,856.33, $267.58 deliberately unbilled."""
    rows = [_row(vendor_name="Clark Crane, LLC", parent_number="NV-13543",
                 parent_public_id="bill-cc", sub_cost_code_name="Miscellaneous",
                 billed_price=6588.75)]
    text = _text_of(_build_toc_basic_pdf(rows))
    assert "$6,588.75" in text
    assert "$6,856.33" not in text


def test_consolidated_credit_stays_negative():
    rows = [
        _row(source_type="BillCreditLineItem", parent_public_id="cr-1", parent_number="55347",
             vendor_name="Jeffco", sub_cost_code_name="Hardwood", billed_price=None, price=3000.00),
        _row(source_type="BillCreditLineItem", parent_public_id="cr-1", parent_number="55347",
             vendor_name="Jeffco", sub_cost_code_name="Hardwood", billed_price=None, price=-303.03),
    ]
    text = _text_of(_build_toc_basic_pdf(rows))
    assert "($3,303.03)" in text
    assert "Total ($3,303.03)" in text


def test_rows_without_a_parent_are_never_merged():
    rows = [
        _row(source_type="Manual", parent_public_id="", parent_number="", billed_price=None, amount=5.00),
        _row(source_type="Manual", parent_public_id="", parent_number="", billed_price=None, amount=7.00),
    ]
    assert len(_consolidate_basic_toc_rows(rows)) == 2


def test_same_number_on_different_parents_is_not_merged():
    rows = [_row(parent_public_id="bill-a"), _row(parent_public_id="bill-b")]
    assert len(_consolidate_basic_toc_rows(rows)) == 2


def test_group_with_no_scc_uses_its_shared_description():
    rows = [_row(sub_cost_code_name="", description="Gravel"),
            _row(sub_cost_code_name=None, description="Gravel")]
    (only,) = _consolidate_basic_toc_rows(rows)
    assert only["sub_cost_code_name"] == "Gravel"


def test_group_with_no_known_amount_prints_a_dash_not_zero():
    rows = [_row(billed_price=None, price=None, amount=None),
            _row(billed_price=None, price=None, amount=None)]
    text = _text_of(_build_toc_basic_pdf(rows))
    assert "—" in text or "—" in text
    assert text.count("$0.00") == 1  # the grand total only
