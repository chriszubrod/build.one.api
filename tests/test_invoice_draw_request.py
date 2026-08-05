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
    # fee_rate 0.14 (14%): the fee ROW is schedule-of-values item 90.000 — a
    # CONSTANT cost-code number, NOT the rate. Distinct values so a regression
    # that renders the rate as the item number (0.14 -> "14.000") fails loudly.
    model = build_cover_rollup([_line(billed_price=100.0)], fee_rate=Decimal("0.14"))
    pdf = build_draw_request_pdf(_header(), model)
    text = _pdf_text(pdf)
    assert "Builder's Fee" in text
    assert "90.000" in text        # fee line item number (constant)
    assert "14.000" not in text    # NOT the rate
    assert "$14.00" in text         # fee = 100 * 0.14
    assert "$114.00" in text        # total = 100 + 14
