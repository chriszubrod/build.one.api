"""Pure tests for Trend PDF renderer (cost-code × draw matrix)."""

import io
from decimal import Decimal

from pypdf import PdfReader

from entities.invoice.business.packet_render import money_number
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
    # No page title (matches the manual packet); the gray band carries the columns.
    assert "CATEGORY" in text
    assert "DESCRIPTION" in text
    assert "TOTAL" in text
    assert "Category" not in text and "Description" not in text
    assert "April 1, 2026" in text
    assert "HA-01" in text
    assert "HA-02" in text
    assert "HA-03" in text
    assert "Site Work" in text
    assert "Subtotal" in text
    assert "Total Due" in text
    assert "Builder's Fee" in text
    assert "90.000" in text

    # Money renders as split "$ | amount" cells — assert the bare amounts.
    assert money_number(row_100) in text
    assert money_number(row_200) in text
    assert money_number(row_300) in text
    assert money_number(row_999) in text
    assert money_number(grand_subtotal) in text

    per_draw_subtotals = [money_number(d["subtotal"]) for d in draws]
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
    assert money_number(Decimal("109.00")) in text  # cell reconciles to subtotal


def test_trend_pdf_coerces_non_str_cost_code_number():
    """Sources may hand back an int cost-code number; it must not crash the sort
    and must still match across draws (str-normalized)."""
    draw = _draw("HA-01", [_cat(100, "Site Work", Decimal("5.00"))],
                 Decimal("5.00"), Decimal("0"), Decimal("5.00"))
    pdf = build_trend_pdf(_header(), [draw])  # would AttributeError if not coerced
    assert pdf[:4] == b"%PDF"


# ---- U-793c: uppercase band, fixed 12-per-page geometry, right-aligned Category ----

import re


def _n_draws(n, amount=Decimal("100.00"), label="D-{:02d}"):
    return [_draw(label.format(i), [_cat("1", "Permits", amount)], amount,
                  Decimal("0"), amount) for i in range(1, n + 1)]


def _pages(pdf: bytes) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages]


def _streams(pdf: bytes) -> list[str]:
    return [p.get_contents().get_data().decode("latin-1") for p in PdfReader(io.BytesIO(pdf)).pages]


def _origins(content: str, text: str) -> list[float]:
    """x of each cell origin (the `cm` before `(text) Tj`) — one per occurrence."""
    pat = r"1 0 0 1 ([\d.]+) [\d.]+ cm\s+q\s+[^()]*?\(" + re.escape(text) + r"\) Tj"
    return [float(x) for x in re.findall(pat, content)]


def test_twelve_draws_per_page_total_on_last_page_only():
    draws = _n_draws(13)
    pages = _pages(build_trend_pdf(_header(), draws))
    assert len(pages) == 2
    assert all(f"D-{i:02d}" in pages[0] for i in range(1, 13))
    assert "D-13" not in pages[0] and "TOTAL" not in pages[0]
    assert "D-13" in pages[1] and "D-12" not in pages[1] and "TOTAL" in pages[1]
    # the Total column is project to date — all 13 draws, not just the last page's
    assert money_number(Decimal("1300.00")) in pages[1]
    # every page repeats the page header and the same cost-code rows
    for page in pages:
        assert "April 1, 2026" in page and "Permits" in page and "CATEGORY" in page


def test_exactly_twelve_draws_is_one_page():
    assert len(_pages(build_trend_pdf(_header(), _n_draws(12)))) == 1


def test_draw_column_width_does_not_stretch_with_fewer_draws():
    """The bug: one draw spread across the whole page. A draw column's position is
    now fixed by its index, whatever the draw count."""
    one = _streams(build_trend_pdf(_header(), _n_draws(1)))[0]
    twelve = _streams(build_trend_pdf(_header(), _n_draws(12)))[0]
    # x of the second label cell in a 12-draw page vs the TOTAL cell after 1 draw:
    # both sit in group slot 2, so a narrow 1-draw page puts TOTAL where D-02 is.
    assert abs(_origins(one, "TOTAL")[0] - _origins(twelve, "D-02")[0]) < 0.5


def test_category_numbers_right_aligned():
    """Matches the Draw Request page: a 1-digit and a 2-digit code end at one x."""
    content = _streams(build_trend_pdf(_header(), _n_draws(1)))[0]
    from reportlab.pdfbase.pdfmetrics import stringWidth

    def right(text):
        m = re.search(r"([\d.]+) 0 Td \(" + re.escape(text) + r"\) Tj", content)
        assert m, text
        return float(m.group(1)) + stringWidth(text, "Times-Roman", 7)

    assert abs(right("1.000") - right("90.000")) < 0.5


def test_million_dollar_draw_and_long_label_stay_in_their_columns():
    """Live data: the largest single draw is $1,765,728.00 and labels run to
    'ONT-MAIN-POST-01'. The figure must render as one unwrapped run; the label may
    wrap at its hyphens but must not lose characters."""
    draws = _n_draws(12, amount=Decimal("1765728.00"), label="ONT-MAIN-POST-{:02d}")
    pdf = build_trend_pdf(_header(), draws)
    pages = _pages(pdf)
    assert len(pages) == 1
    content = _streams(pdf)[0]
    assert content.count("(1,765,728.00) Tj") >= 12 * 3  # row + subtotal + total due
    # the project-to-date column holds 8 digits (live max is $19,954,088.97)
    assert content.count("(21,188,736.00) Tj") == 3
    flat = pages[0].replace("\n", "")
    for i in range(1, 13):
        assert f"ONT-MAIN-POST-{i:02d}" in flat


def test_large_credit_figure_never_breaks_mid_number():
    """A $1M+ credit prints in parentheses, wider than a draw column: it may crowd
    its neighbour but must stay one run, never split digits across lines."""
    draws = _n_draws(12, amount=Decimal("-1765728.00"))
    content = _streams(build_trend_pdf(_header(), draws))[0]
    assert content.count("(\\(1,765,728.00\\)) Tj") >= 12 * 3
