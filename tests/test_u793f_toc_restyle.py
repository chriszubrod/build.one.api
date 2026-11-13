"""U-793f: the two Tables of Contents in the packet house style.

Summary = one row per source document; Detail = every line grouped by Category under a
"2.000  Dumpsters" heading. Both carry the shared packet header, distinct titles and
the uppercase gray band. Rows and amounts are pinned by test_invoice_toc_basic_per_source.
"""
import io
import re

import pytest

pytest.importorskip("reportlab")
from pypdf import PdfReader

from entities.invoice.api.router import _build_toc_basic_pdf, _build_toc_expanded_pdf

HEADER = {
    "project": "WR - 5224 Williamsburg Road",
    "application_no": "WR-01",
    "application_date": "10/30/2026",
    "period_to": "10/15/2026",
    "from_lines": ["Rogers Build, Inc.", "PO Box 594", "Brentwood, TN 37024"],
    "to_name": "Chesnut Infill GP",
    "to_lines": [],
}


def _row(cc, cc_name, vendor, number, desc, amount, source_type="ExpenseLineItem", pid=None):
    return {
        "source_type": source_type, "source_date": "09-03-2026", "vendor_name": vendor,
        "parent_number": number, "parent_public_id": pid or number,
        "cost_code_number": cc, "cost_code_name": cc_name,
        "sub_cost_code_name": desc, "billed_price": amount,
    }


ROWS = [
    _row("01", "Permits", "City of Brentwood", "QBO-1", "Demo & Land Dist.Permit", 257.48),
    _row("02", "Dumpsters", "VaVia Nashville, LLC", "QBO-2", "Dumpsters", 900.00),
    _row("02", "Dumpsters", "VaVia Nashville, LLC", "QBO-3", "Dumpsters", 1095.95),
    _row("06", "Grading/Sitework/Demolition", "Robinson Demolition, LLC", "2103",
         "Grading/Sitework/Demolition", 19500.00, source_type="BillLineItem"),
]


def _lines(pdf: bytes) -> list[str]:
    return [ln for p in PdfReader(io.BytesIO(pdf)).pages for ln in (p.extract_text() or "").splitlines()]


def _text(pdf: bytes) -> str:
    return re.sub(r"\s+", " ", " ".join(_lines(pdf)))


@pytest.mark.parametrize("build,title,other", [
    (_build_toc_basic_pdf, "Table of Contents Summary", "Table of Contents Detail"),
    (_build_toc_expanded_pdf, "Table of Contents Detail", "Table of Contents Summary"),
])
def test_distinct_titles_shared_header_uppercase_band(build, title, other):
    text = _text(build(ROWS, HEADER))
    assert title in text and other not in text
    for strip in ("PROJECT", "WR - 5224 Williamsburg Road", "APPLICATION NO.", "WR-01",
                  "PERIOD TO", "Rogers Build, Inc.", "Chesnut Infill GP"):
        assert strip in text
    for band in ("DATE", "VENDOR", "INVOICE", "DESCRIPTION", "TYPE", "AMOUNT"):
        assert band in text
    assert "Cost Code" not in text and "Table Of Contents" not in text


def test_without_a_header_still_renders_the_title():
    assert "Table of Contents Summary" in _text(_build_toc_basic_pdf(ROWS))


def test_detail_groups_under_named_category_headings():
    lines = _lines(_build_toc_expanded_pdf(ROWS, HEADER))
    for heading in ("1.000 Permits", "2.000 Dumpsters", "6.000 Grading/Sitework/Demolition"):
        assert any(re.sub(r"\s+", " ", ln).strip() == heading for ln in lines), heading
    # the code is a heading, not a column repeated on every line
    assert not any(ln.strip().startswith(("01 ", "02 ", "06 ")) for ln in lines)
    text = _text(_build_toc_expanded_pdf(ROWS, HEADER))
    assert text.count("Subtotal") == 3
    assert "Subtotal $1,995.95" in text and "Total $21,753.43" in text


def test_long_description_is_not_split_mid_word():
    """The old Helvetica Detail split it as 'Grading/Sitework/Demolit' + 'ion'."""
    for build in (_build_toc_basic_pdf, _build_toc_expanded_pdf):
        lines = _lines(build(ROWS, HEADER))
        assert not any(ln.strip() == "ion" or ln.rstrip().endswith("Demolit") for ln in lines)


def test_total_never_prints_alone_on_a_page():
    """A page break may not fall within the last 3 rows + Total: on the Summary that
    is 3 document rows; on the Detail, 2 lines + the group's Subtotal."""
    multi = 0
    for n in range(30, 90):
        rows = [_row("02", "Dumpsters", f"Vendor {i}", f"N-{i}", "Dumpsters", 1.0) for i in range(n)]
        for build, need in ((_build_toc_basic_pdf, 3), (_build_toc_expanded_pdf, 2)):
            pages = PdfReader(io.BytesIO(build(rows, HEADER))).pages
            if len(pages) == 1:
                continue
            multi += 1
            last = {ln.strip() for ln in pages[-1].extract_text().splitlines()}
            assert sum(f"Vendor {i}" in last for i in range(n)) >= need, (build.__name__, n)
    assert multi > 20  # the sweep really crosses page breaks


@pytest.mark.parametrize("build", [_build_toc_basic_pdf, _build_toc_expanded_pdf])
def test_dates_print_like_the_rest_of_the_packet(build):
    """U-793g: M/D/YYYY (as in the header strip), not the stored MM-DD-YYYY."""
    lines = {ln.strip() for ln in _lines(build(ROWS, HEADER))}
    assert "9/3/2026" in lines
    assert "09-03-2026" not in lines


def test_unparseable_date_prints_unchanged():
    from entities.invoice.api.router import _toc_display_date
    assert _toc_display_date("09-18-2026") == "9/18/2026"
    assert _toc_display_date("") == ""
    assert _toc_display_date(None) == ""
    assert _toc_display_date("sometime") == "sometime"
