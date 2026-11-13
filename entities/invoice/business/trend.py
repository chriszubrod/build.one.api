"""Pure ReportLab renderer for a construction Trend page (per cost-code × draw matrix).

Shares the packet house style — serif type, the Rogers Build mark top-right, a gray
column-header band, 3-decimal Category numbers, and split ``$ | amount`` money cells
(an empty cell renders ``$  -``). Landscape, with a FIXED column geometry (U-793c):
every data column has a fixed width (DRAWS_PER_PAGE draw columns plus a Total column
fill a page) and the money columns are anchored to the right edge. A blank spacer
column between Description and the money columns absorbs the slack (U-793d) — no
data column ever stretches, so a 1-draw project prints normal-width columns.
Draws 1-12 print on page 1, 13-24 on page 2, and so on; every page carries the same
cost-code rows (the union over ALL draws) and the Total column — project to date —
prints once, on the last page.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Optional

from entities.invoice.business.cover import _cost_code_sort_key
from entities.invoice.business.packet_render import (
    BAND_FILL,
    SERIF,
    SERIF_BOLD,
    as_decimal as _as_decimal,
    format_cc_number,
    logo_flowable,
    money_number,
)

FEE_ITEM_NUMBER = "90"

# Draw columns per page (U-793c): pay applications 1-12 on page 1, 13-24 on page 2...
DRAWS_PER_PAGE = 12


def _cc_num(cat: dict) -> str:
    """Cost-code number of a category cell as a string. Sources may hand back an
    int; the sort key and the cross-draw cell match both need a consistent str."""
    number = cat.get("cost_code_number")
    return str(number) if number is not None else ""


def _category_amount_for_draw(draw: dict, cost_code_number: str) -> Optional[Decimal]:
    """This draw's amount for a cost code — the SUM of all matching category cells,
    so a draw whose categories aren't pre-grouped by number still reconciles to its
    own subtotal (the canonical producer build_cover_rollup pre-groups, but the
    renderer must not silently drop a duplicate). None when the code is absent or
    all its cells are None → renders an empty ($  -) cell."""
    total: Optional[Decimal] = None
    for cat in draw.get("categories") or []:
        if _cc_num(cat) == cost_code_number:
            amt = cat.get("amount")
            if amt is not None:
                total = (total or Decimal("0")) + _as_decimal(amt)
    return total


def _union_cost_codes(draws: list[dict]) -> list[tuple[str, str]]:
    names: dict[str, str] = {}
    for draw in draws:
        for cat in draw.get("categories") or []:
            number = _cc_num(cat)
            name = cat.get("cost_code_name") or ""
            if number not in names or (not names[number] and name):
                names[number] = name
    ordered = sorted(names.keys(), key=_cost_code_sort_key)
    return [(n, names[n]) for n in ordered]


def build_trend_pdf(header: dict, draws: list[dict]) -> bytes:
    """Landscape Trend table: cost codes × draw columns (DRAWS_PER_PAGE per page) +
    a project-to-date Total column on the last page."""
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    BLACK = colors.black
    BAND = colors.HexColor(BAND_FILL)
    # 7pt: a $1M+ draw (live max $1,765,728.00) must fit one of 12 fixed draw columns.
    FONT_SIZE = 7

    def esc(text) -> str:
        return _html.escape(str(text)) if text not in (None, "") else ""

    wrap_style = ParagraphStyle("tr_wrap", fontName=SERIF, fontSize=FONT_SIZE,
                                leading=FONT_SIZE + 2)
    # Category numbers right-aligned, matching the Draw Request (Invoice) page.
    cat_style = ParagraphStyle("tr_cat", fontName=SERIF, fontSize=FONT_SIZE,
                               leading=FONT_SIZE + 2, alignment=TA_RIGHT)
    band_style = ParagraphStyle("tr_band", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=FONT_SIZE + 2)
    # A draw label is an invoice number (up to "ONT-MAIN-POST-01"); let it wrap at
    # its own hyphens rather than overrun a fixed-width column.
    band_right = ParagraphStyle("tr_band_r", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=FONT_SIZE + 2, alignment=TA_RIGHT,
                                embeddedHyphenation=1)
    # splitLongWords=0 on figures: a too-wide one (a $1M+ credit prints as
    # "(1,765,728.00)") must overrun whole, never break mid-number.
    money_style = ParagraphStyle("tr_money", fontName=SERIF, fontSize=FONT_SIZE,
                                 leading=FONT_SIZE + 2, alignment=TA_RIGHT, splitLongWords=0)
    dollar_style = ParagraphStyle("tr_dollar", fontName=SERIF, fontSize=FONT_SIZE,
                                  leading=FONT_SIZE + 2)
    bold_right = ParagraphStyle("tr_bold_r", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=FONT_SIZE + 2, alignment=TA_RIGHT, splitLongWords=0)
    bold_dollar = ParagraphStyle("tr_bold_d", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                 leading=FONT_SIZE + 2)
    date_style = ParagraphStyle("tr_date", fontName=SERIF, fontSize=10, leading=13,
                                alignment=TA_CENTER)
    from_style = ParagraphStyle("tr_from", fontName=SERIF, fontSize=9, leading=11.5,
                                alignment=TA_LEFT)

    date_str = header.get("date") or ""
    to_name = header.get("to_name") or ""
    from_lines = list(header.get("from_lines") or [])
    to_lines = list(header.get("to_lines") or [])

    page_w = landscape(letter)[0]
    margin = 0.4 * inch
    usable_w = page_w - 2 * margin

    # ---- header: Date over From/To, logo top-right (no page title) ---------
    from_body = "<br/>".join(esc(ln) for ln in from_lines if ln)
    to_parts = [f"To:&nbsp;&nbsp;{esc(to_name)}"] if to_name else ["To:"]
    to_parts.extend(esc(ln) for ln in to_lines if ln)
    to_body = "<br/>".join(to_parts)

    def make_header() -> Table:
        """A fresh header per page — a flowable instance can't be laid out twice."""
        left_w = usable_w * 0.80
        logo = logo_flowable(usable_w * 0.18, 1.0 * inch)
        from_to = Table(
            [[Paragraph(from_body, from_style), Paragraph(to_body, from_style)]],
            colWidths=[left_w * 0.45, left_w * 0.55],
        )
        from_to.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        left_stack = Table(
            [[Paragraph(f"Date:&nbsp;&nbsp;&nbsp;{esc(date_str)}", date_style)], [from_to]],
            colWidths=[left_w],
        )
        left_stack.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (0, 0), 8),
        ]))
        header_table = Table([[left_stack, logo or ""]], colWidths=[left_w, usable_w - left_w])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        return header_table

    # ---- matrix: each money group is two physical columns ($, value) -------
    # Fixed geometry: no data column stretches. Category + Description sit on the
    # left edge, the money groups (DRAWS_PER_PAGE draw groups + one wider Total
    # group fill the page) are anchored to the right edge, and a blank SPACER
    # column between them absorbs the slack on a page with fewer draws (U-793d).
    # The table always spans the full page, so the gray band does too.
    cat_w = 0.65 * inch          # "CATEGORY" on one line
    desc_w = 1.35 * inch         # "Grading/Sitework/Demolition" on one line
    dollar_w = 5                 # points, like total_group_w
    total_group_w = 52           # project to date: live max $19,954,088.97 -> to $99,999,999.99
    group_w = (usable_w - cat_w - desc_w - total_group_w) / DRAWS_PER_PAGE

    def money_cells(value, *, bold=False):
        dsty = bold_dollar if bold else dollar_style
        vsty = bold_right if bold else money_style
        return [Paragraph("$", dsty), Paragraph(money_number(value), vsty)]

    cost_codes = _union_cost_codes(draws)
    amounts = {(id(d), n): _category_amount_for_draw(d, n) for d in draws for n, _ in cost_codes}
    row_totals = {n: sum((amounts[(id(d), n)] for d in draws if amounts[(id(d), n)] is not None),
                         Decimal("0"))
                  for n, _ in cost_codes}
    grand_subtotal = sum((_as_decimal(d.get("subtotal", 0)) for d in draws), Decimal("0"))
    grand_fee = sum((_as_decimal(d.get("builders_fee", 0)) for d in draws), Decimal("0"))
    grand_total = sum((_as_decimal(d.get("total", 0)) for d in draws), Decimal("0"))

    def page_table(page_draws: list[dict], with_total: bool) -> Table:
        groups = len(page_draws) + (1 if with_total else 0)
        group_widths = [group_w] * len(page_draws) + ([total_group_w] if with_total else [])
        spacer_w = usable_w - cat_w - desc_w - sum(group_widths)  # 0 on a full page
        col_widths = [cat_w, desc_w, spacer_w]
        for gw in group_widths:
            col_widths += [dollar_w, gw - dollar_w]

        band_row: list = [Paragraph("CATEGORY", band_style), Paragraph("DESCRIPTION", band_style), ""]
        for draw in page_draws:
            band_row += [Paragraph(esc(draw.get("label") or ""), band_right), ""]
        if with_total:
            band_row += [Paragraph("TOTAL", band_right), ""]
        table_data: list[list] = [band_row]

        for number, name in cost_codes:
            row: list = [Paragraph(esc(format_cc_number(number, 3)), cat_style),
                         Paragraph(esc(name), wrap_style), ""]
            for draw in page_draws:
                row += money_cells(amounts[(id(draw), number)])
            if with_total:
                row += money_cells(row_totals[number])
            table_data.append(row)

        def figures_row(first, label_para, key, grand, *, bold):
            row = [first, label_para, ""]
            for draw in page_draws:
                row += money_cells(_as_decimal(draw.get(key, 0)), bold=bold)
            if with_total:
                row += money_cells(grand, bold=bold)
            return row

        table_data.append(figures_row("", Paragraph("Subtotal", bold_right), "subtotal",
                                      grand_subtotal, bold=True))
        subtotal_idx = len(table_data) - 1
        table_data.append(figures_row(Paragraph(format_cc_number(FEE_ITEM_NUMBER, 3), cat_style),
                                      Paragraph("Builder's Fee", wrap_style), "builders_fee",
                                      grand_fee, bold=False))
        table_data.append(figures_row("", Paragraph("Total Due", bold_right), "total",
                                      grand_total, bold=True))
        total_idx = len(table_data) - 1

        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("TOPPADDING", (0, 0), (-1, 0), 5),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
            ("TOPPADDING", (0, 1), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            # the spacer is blank and may be 0 wide (a full page): no padding
            ("LEFTPADDING", (2, 0), (2, -1), 0),
            ("RIGHTPADDING", (2, 0), (2, -1), 0),
            # ...and its empty cells must not take the table's default 10pt leading,
            # which would make every row taller than the 7pt figures need
            ("FONTSIZE", (2, 0), (2, -1), FONT_SIZE),
            ("LEADING", (2, 0), (2, -1), FONT_SIZE + 2),
            # Subtotal / Total Due labels span Description + spacer so they sit
            # against their figures, as on the Draw Request page
            ("SPAN", (1, subtotal_idx), (2, subtotal_idx)),
            ("SPAN", (1, total_idx), (2, total_idx)),
        ]
        # each money group: SPAN its band label across ($, value); hug $ to value
        for g in range(groups):
            dcol = 3 + 2 * g
            vcol = dcol + 1
            style_cmds += [
                ("SPAN", (dcol, 0), (vcol, 0)),
                ("LEFTPADDING", (dcol, 0), (dcol, -1), 1),
                ("RIGHTPADDING", (dcol, 0), (dcol, -1), 0),
                ("LEFTPADDING", (vcol, 0), (vcol, -1), 0),
                ("RIGHTPADDING", (vcol, 0), (vcol, -1), 2),
                # the band label spans ($, value) and takes the $ cell's padding:
                # end it where the figures end (cf. U-793b on the Draw Request)
                ("RIGHTPADDING", (dcol, 0), (dcol, 0), 2),
                ("LINEABOVE", (dcol, subtotal_idx), (vcol, subtotal_idx), 0.6, BLACK),
                ("LINEABOVE", (dcol, total_idx), (vcol, total_idx), 0.6, BLACK),
            ]
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle(style_cmds))
        return table

    chunks = [draws[i:i + DRAWS_PER_PAGE] for i in range(0, len(draws), DRAWS_PER_PAGE)] or [[]]
    story: list = []
    for page_no, chunk in enumerate(chunks):
        if page_no:
            story.append(PageBreak())
        story += [make_header(), Spacer(1, 0.2 * inch),
                  page_table(chunk, with_total=page_no == len(chunks) - 1)]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        leftMargin=margin, rightMargin=margin,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    doc.build(story)
    return buf.getvalue()
