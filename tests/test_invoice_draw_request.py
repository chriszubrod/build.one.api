"""Pure tests for draw request PDF renderer."""

import io
from decimal import Decimal

from pypdf import PdfReader

from entities.invoice.business.cover import build_cover_rollup
from entities.invoice.business.draw_request import build_draw_request_pdf


def _line(**kwargs):
    row = {
        "source_type": "BillLineItem",
        "cost_code_number": "100",
        "cost_code_name": "Site Work",
        "billed_price": 1000.0,
        "is_credit": False,
    }
    row.update(kwargs)
    return row


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "".join(page.extract_text() or "" for page in reader.pages)


def _header(**overrides):
    base = {
        "from_lines": ["Rogers Build, Inc.", "123 Main St"],
        "to_name": "Acme Owner LLC",
        "to_lines": ["456 Oak Ave"],
        "draw_number": "DR-2026-04",
        "date": "April 1, 2026",
    }
    base.update(overrides)
    return base


def test_draw_request_pdf_core_content():
    model = build_cover_rollup([_line(billed_price=2500.0)], fee_rate=None)
    pdf = build_draw_request_pdf(_header(), model)
    assert pdf[:4] == b"%PDF"
    text = _pdf_text(pdf)
    assert "DR-2026-04" in text
    assert "Site Work" in text
    assert "Subtotal" in text
    assert "Total Due" in text
    assert "Please remit payments to Rogers Build, Inc. by bank wire." in text


def test_draw_request_pdf_no_fee_row_when_fee_rate_none():
    model = build_cover_rollup([_line(billed_price=100.0)], fee_rate=None)
    pdf = build_draw_request_pdf(_header(), model)
    text = _pdf_text(pdf)
    assert "Builder" not in text


def test_draw_request_pdf_includes_builders_fee_when_rate_set():
    # fee_rate 0.14 (14%): the fee ROW is schedule-of-values item 90 — a CONSTANT
    # cost-code number, rendered like every Category (3 decimals -> "90.000"), NOT
    # the rate. Distinct values so a regression that renders the rate as the item
    # number (0.14 -> "14.000") fails loudly.
    from entities.invoice.business.draw_request import FEE_ITEM_NUMBER
    assert FEE_ITEM_NUMBER == "90"   # constant item number, formatted to "90.000" at render
    model = build_cover_rollup([_line(billed_price=100.0)], fee_rate=Decimal("0.14"))
    pdf = build_draw_request_pdf(_header(), model)
    text = _pdf_text(pdf)
    assert "Builder's Fee" in text
    assert "90.000" in text         # fee item number 90 rendered 3-decimal (matches Categories)
    assert "14.000" not in text     # NOT the rate
    # Money renders as a split "$ | amount" cell, so assert the bare amount (the "$"
    # lives in its own column and need not be contiguous with the number in text).
    assert "14.00" in text          # fee = 100 * 0.14
    assert "114.00" in text         # total = 100 + 14


def _right_edge(content: str, text: str, font: str, size: float) -> float:
    """Right edge (cell origin + right-align offset + glyph width) of the first
    `(text) Tj` in a reportlab page stream. pypdf's visitor_text does not report
    the Td offset a right-aligned Paragraph uses, so read the operators directly."""
    import re
    from reportlab.pdfbase.pdfmetrics import stringWidth

    m = re.search(r"1 0 0 1 ([\d.]+) [\d.]+ cm\s+q\s+[^()]*?([\d.]+) 0 Td \(" + re.escape(text) + r"\) Tj",
                  content)
    assert m, f"{text!r} not drawn as a single right-aligned run"
    return float(m.group(1)) + float(m.group(2)) + stringWidth(text, font, size)


def test_amount_header_right_edge_aligns_with_figures():
    """U-793b: the AMOUNT header spans the $ and amount columns, and a span takes its
    padding from its top-left ($) cell, whose right padding is zeroed so the $ hugs
    its number. The header sat flush on the band's right edge; it must end where the
    figures below it end."""
    model = build_cover_rollup([_line(billed_price=2500.0)], fee_rate=None)
    page = PdfReader(io.BytesIO(build_draw_request_pdf(_header(), model))).pages[0]
    content = page.get_contents().get_data().decode("latin-1")
    header = _right_edge(content, "AMOUNT", "Times-Bold", 10)
    figure = _right_edge(content, "2,500.00", "Times-Roman", 9.5)
    assert abs(header - figure) < 0.5, (header, figure)
