"""Pure tests for Trend PDF renderer (cost-code × draw matrix)."""

import io
from decimal import Decimal

from pypdf import PdfReader

from entities.invoice.business.cover import build_cover_rollup
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


def test_eleven_draws_plus_total_fit_one_page():
    pages = _pages(build_trend_pdf(_header(), _n_draws(11)))
    assert len(pages) == 1 and "D-11" in pages[0] and "TOTAL" in pages[0]


def test_twelve_draws_fill_page_one_and_total_gets_its_own_page():
    """U-793e: at 0.75in margins a page holds 12 draws OR 11 + TOTAL. Draws never
    move pages as a project accrues them, so a full last page pushes TOTAL on."""
    pages = _pages(build_trend_pdf(_header(), _n_draws(12)))
    assert len(pages) == 2
    assert all(f"D-{i:02d}" in pages[0] for i in range(1, 13)) and "TOTAL" not in pages[0]
    assert "TOTAL" in pages[1] and "D-" not in pages[1]
    assert money_number(Decimal("1200.00")) in pages[1]
    assert len(_pages(build_trend_pdf(_header(), _n_draws(24)))) == 3
    assert len(_pages(build_trend_pdf(_header(), _n_draws(25)))) == 3


def test_draw_column_width_does_not_stretch_with_fewer_draws():
    """The bug: one draw spread across the whole page. Money columns have a fixed
    width and are anchored to the right edge (U-793d) — Description takes the slack."""
    one = _streams(build_trend_pdf(_header(), _n_draws(1)))[0]
    eleven = _streams(build_trend_pdf(_header(), _n_draws(11)))[0]
    group_w = _origins(eleven, "D-02")[0] - _origins(eleven, "D-01")[0]
    # TOTAL sits at the same right-anchored x whatever the draw count ...
    assert abs(_origins(one, "TOTAL")[0] - _origins(eleven, "TOTAL")[0]) < 0.5
    # ... and the single draw column is exactly one fixed group wide, not stretched
    assert abs((_origins(one, "TOTAL")[0] - _origins(one, "D-01")[0]) - group_w) < 0.5


def test_no_data_column_stretches_a_blank_spacer_takes_the_slack():
    """U-793d: Category and Description keep their fixed widths whatever the draw
    count; the slack is a blank spacer between Description and the money columns,
    and the Subtotal / Total Due labels still sit against their figures."""
    one_pdf = build_trend_pdf(_header(), _n_draws(1))
    twelve_pdf = build_trend_pdf(_header(), _n_draws(12))
    one, twelve = _streams(one_pdf)[0], _streams(twelve_pdf)[0]
    for label in ("CATEGORY", "DESCRIPTION"):
        assert abs(_origins(one, label)[0] - _origins(twelve, label)[0]) < 0.5
    # Description is NOT stretched on a 1-draw page: a long name still wraps
    long_name = "Grading Sitework Demolition Excavation Hauling Backfill Compaction"
    d = [_draw("D-01", [_cat("1", long_name, Decimal("1"))], Decimal("1"), Decimal("0"), Decimal("1"))]
    page_lines = _pages(build_trend_pdf(_header(), d))[0].split("\n")
    first = next(ln for ln in page_lines if "Grading" in ln)
    assert "Compaction" not in first
    # Subtotal label ends where the first draw group begins, on a 1-draw page too
    m = re.search(r"1 0 0 1 ([\d.]+) [\d.]+ cm\s+q\s+[^()]*?([\d.]+) 0 Td \(Subtotal\) Tj", one)
    from reportlab.pdfbase.pdfmetrics import stringWidth
    subtotal_right = float(m.group(1)) + float(m.group(2)) + stringWidth("Subtotal", "Times-Bold", 7)
    first_dollar = min(_origins(one, "$"))
    assert 0 <= first_dollar - subtotal_right < 10


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
    'ONT-MAIN-POST-01'. Each figure must render as one unwrapped run, on a full
    12-draw page and on an 11 + TOTAL page; the label may wrap at its hyphens but
    must not lose characters."""
    amount = Decimal("1765728.00")
    for n in (11, 12):
        draws = _n_draws(n, amount=amount, label="ONT-MAIN-POST-{:02d}")
        pdf = build_trend_pdf(_header(), draws)
        content = _streams(pdf)[0]
        assert content.count("(1,765,728.00) Tj") >= n * 3  # row + subtotal + total due
        flat = _pages(pdf)[0].replace("\n", "")
        for i in range(1, n + 1):
            assert f"ONT-MAIN-POST-{i:02d}" in flat
    # the project-to-date column holds 8 digits (live max is $19,954,088.97)
    content = _streams(build_trend_pdf(_header(), _n_draws(11, amount=amount)))[0]
    assert content.count("(19,423,008.00) Tj") == 3


def test_large_credit_figure_never_breaks_mid_number():
    """A $1M+ credit prints in parentheses, wider than a draw column: it may crowd
    its neighbour but must stay one run, never split digits across lines."""
    draws = _n_draws(12, amount=Decimal("-1765728.00"))
    content = _streams(build_trend_pdf(_header(), draws))[0]
    assert content.count("(\\(1,765,728.00\\)) Tj") >= 12 * 3


def test_spacer_does_not_make_rows_taller():
    """The blank spacer's empty cells must not raise the row height: a cost-code row
    is its 7pt text (9pt leading) + 3pt top + 3pt bottom padding = 15pt — not the
    18pt an empty cell at the table's default 10pt/12pt leading would force."""
    def gap(n):
        d = [_draw(f"D-{i:02d}", [_cat("1", "Permits", Decimal("1")), _cat("2", "Dumpsters", Decimal("1"))],
                   Decimal("2"), Decimal("0"), Decimal("2")) for i in range(1, n + 1)]
        content = _streams(build_trend_pdf(_header(), d))[0]
        ys = []
        for text in ("1.000", "2.000"):
            m = re.search(r"1 0 0 1 [\d.]+ ([\d.]+) cm\s+q\s+[^()]*?\(" + re.escape(text) + r"\) Tj", content)
            ys.append(float(m.group(1)))
        return ys[0] - ys[1]
    assert abs(gap(1) - 15) < 0.5 and abs(gap(12) - 15) < 0.5


def _page_x(pdf: bytes, text: str) -> float:
    """Absolute x where a LEFT-aligned run starts (pypdf's accumulated cm + tm).
    Only for left-aligned text: a right-aligned Paragraph's Td offset is not reported."""
    xs = []
    def visit(t, cm, tm, *_):
        if t.strip() == text:
            xs.append(cm[4] + tm[4])
    PdfReader(io.BytesIO(pdf)).pages[0].extract_text(visitor_text=visit)
    return xs[0]


def test_trend_and_invoice_share_the_packet_margins():
    """U-793e: every packet page uses 0.75in sides / 0.5in top-bottom. The gray
    band's CATEGORY cell starts at the 54pt left margin on the Trend (3pt cell
    padding) and on the Draw Request / Invoice page (4pt cell padding)."""
    from entities.invoice.business import packet_render as pr
    from entities.invoice.business.draw_request import build_draw_request_pdf

    assert (pr.PAGE_SIDE_MARGIN_IN, pr.PAGE_TOPBOTTOM_MARGIN_IN) == (0.75, 0.5)
    trend = build_trend_pdf(_header(), _n_draws(1))
    assert abs(_page_x(trend, "CATEGORY") - 3 - 54) < 0.5
    invoice = build_draw_request_pdf(
        {"from_lines": ["A"], "to_name": "B", "to_lines": [], "draw_number": "X-01", "date": "1/1/2026"},
        build_cover_rollup([], fee_rate=None),
    )
    assert abs(_page_x(invoice, "CATEGORY") - 4 - 54) < 0.5
