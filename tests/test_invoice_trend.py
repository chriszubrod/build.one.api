"""Pure tests for Trend PDF renderer (cost-code × draw matrix)."""

import io
from decimal import Decimal

from pypdf import PdfReader

from entities.invoice.business.cover import _format_money
from entities.invoice.business.trend import build_trend_pdf


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "".join(page.extract_text() or "" for page in reader.pages)


def _header(**overrides):
    base = {
        "from_lines": ["Rogers Build, Inc.", "123 Main St"],
        "to_name": "Acme Owner LLC",
        "to_lines": ["456 Oak Ave"],
        "date": "April 1, 2026",
    }
    base.update(overrides)
    return base


def _draw(label, categories, subtotal, builders_fee, total):
    return {
        "label": label,
        "categories": categories,
        "subtotal": subtotal,
        "builders_fee": builders_fee,
        "total": total,
    }


def _cat(number, name, amount):
    return {
        "cost_code_number": number,
        "cost_code_name": name,
        "amount": amount,
    }


def test_trend_pdf_matrix_and_reconciliation():
    draws = [
        _draw(
            "HA-01",
            [
                _cat("100", "Site Work", Decimal("1000.00")),
                _cat("200", "Framing", Decimal("500.00")),
            ],
            Decimal("1500.00"),
            Decimal("150.00"),
            Decimal("1650.00"),
        ),
        _draw(
            "HA-02",
            [
                _cat("100", "Site Work", Decimal("200.00")),
                _cat("300", "Roofing", Decimal("800.00")),
            ],
            Decimal("1000.00"),
            Decimal("100.00"),
            Decimal("1100.00"),
        ),
        _draw(
            "HA-03",
            [
                _cat("200", "Framing", Decimal("300.00")),
                _cat("999", "Non-Numeric Sort Last", Decimal("50.00")),
            ],
            Decimal("350.00"),
            Decimal("35.00"),
            Decimal("385.00"),
        ),
    ]

    row_100 = Decimal("1200.00")
    row_200 = Decimal("800.00")
    row_300 = Decimal("800.00")
    row_999 = Decimal("50.00")
    grand_subtotal = sum(d["subtotal"] for d in draws)
    assert grand_subtotal == Decimal("2850.00")

    pdf = build_trend_pdf(_header(), draws)
    assert pdf[:4] == b"%PDF"

    text = _pdf_text(pdf)
    assert "Trend" in text
    assert "April 1, 2026" in text
    assert "HA-01" in text
    assert "HA-02" in text
    assert "HA-03" in text
    assert "Site Work" in text
    assert "Subtotal" in text
    assert "Total Due" in text
    assert "Builder's Fee" in text
    assert "90.000" in text

    assert _format_money(row_100) in text
    assert _format_money(row_200) in text
    assert _format_money(row_300) in text
    assert _format_money(row_999) in text
    assert _format_money(grand_subtotal) in text

    per_draw_subtotals = [_format_money(d["subtotal"]) for d in draws]
    for fmt in per_draw_subtotals:
        assert fmt in text


def test_trend_pdf_sums_duplicate_cost_codes_within_a_draw():
    """A draw whose categories aren't pre-grouped (two rows for the same cost
    code) must SUM into one cell, not drop the second — so the matrix reconciles
    to the draw's subtotal instead of silently losing money."""
    from entities.invoice.business.trend import _category_amount_for_draw, _union_cost_codes

    draw = _draw(
        "HA-01",
        [_cat("100", "Site Work", Decimal("10.00")), _cat("100", "Site Work", Decimal("99.00"))],
        Decimal("109.00"), Decimal("0"), Decimal("109.00"),
    )
    assert _category_amount_for_draw(draw, "100") == Decimal("109.00")
    assert _union_cost_codes([draw]) == [("100", "Site Work")]
    text = _pdf_text(build_trend_pdf(_header(), [draw]))
    assert _format_money(Decimal("109.00")) in text  # cell reconciles to subtotal


def test_trend_pdf_coerces_non_str_cost_code_number():
    """Sources may hand back an int cost-code number; it must not crash the sort
    and must still match across draws (str-normalized)."""
    draw = _draw("HA-01", [_cat(100, "Site Work", Decimal("5.00"))],
                 Decimal("5.00"), Decimal("0"), Decimal("5.00"))
    pdf = build_trend_pdf(_header(), [draw])  # would AttributeError if not coerced
    assert pdf[:4] == b"%PDF"
