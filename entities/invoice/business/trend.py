"""Pure ReportLab renderer for a construction Trend page (per cost-code × draw matrix).

Shares the packet house style — serif type, the Rogers Build mark top-right, a gray
column-header band, 3-decimal Category numbers, and split ``$ | amount`` money cells
(an empty cell renders ``$  -``). Kept in landscape so the draw columns keep fitting
as a project accumulates draws.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any, Optional

from entities.invoice.business.cover import _cost_code_sort_key
from entities.invoice.business.packet_render import (
    BAND_FILL,
    SERIF,
    SERIF_BOLD,
    format_cc_number,
    logo_flowable,
    money_number,
)

FEE_ITEM_NUMBER = "90"


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


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
    """Landscape Trend table: cost codes × draw columns + row/grand totals."""
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    BLACK = colors.black
    BAND = colors.HexColor(BAND_FILL)
    FONT_SIZE = 7.5

    def esc(text) -> str:
        return _html.escape(str(text)) if text not in (None, "") else ""

    wrap_style = ParagraphStyle("tr_wrap", fontName=SERIF, fontSize=FONT_SIZE,
                                leading=FONT_SIZE + 2)
    cat_style = ParagraphStyle("tr_cat", fontName=SERIF, fontSize=FONT_SIZE,
                               leading=FONT_SIZE + 2, alignment=TA_LEFT)
    band_style = ParagraphStyle("tr_band", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=FONT_SIZE + 2)
    band_right = ParagraphStyle("tr_band_r", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=FONT_SIZE + 2, alignment=TA_RIGHT)
    money_style = ParagraphStyle("tr_money", fontName=SERIF, fontSize=FONT_SIZE,
                                 leading=FONT_SIZE + 2, alignment=TA_RIGHT)
    dollar_style = ParagraphStyle("tr_dollar", fontName=SERIF, fontSize=FONT_SIZE,
                                  leading=FONT_SIZE + 2)
    bold_right = ParagraphStyle("tr_bold_r", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=FONT_SIZE + 2, alignment=TA_RIGHT)
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
    margin = 0.5 * inch
    usable_w = page_w - 2 * margin

    # ---- header: Date over From/To, logo top-right (no page title) ---------
    from_body = "<br/>".join(esc(ln) for ln in from_lines if ln)
    to_parts = [f"To:&nbsp;&nbsp;{esc(to_name)}"] if to_name else ["To:"]
    to_parts.extend(esc(ln) for ln in to_lines if ln)
    to_body = "<br/>".join(to_parts)

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

    # ---- matrix: each money group is two physical columns ($, value) -------
    n_draws = len(draws)
    groups = n_draws + 1  # draws + Total
    cat_w = 0.55 * inch
    desc_w = 1.5 * inch
    group_w = max(0.62 * inch, (usable_w - cat_w - desc_w) / groups)
    dollar_w = 0.14 * inch
    value_w = group_w - dollar_w
    col_widths = [cat_w, desc_w] + [dollar_w, value_w] * groups

    def money_cells(value, *, bold=False, dash_when_none=True):
        dsty = bold_dollar if bold else dollar_style
        vsty = bold_right if bold else money_style
        text = money_number(value) if (value is not None or dash_when_none) else ""
        return [Paragraph("$", dsty), Paragraph(text, vsty)]

    # header band
    band_row: list = [Paragraph("Category", band_style), Paragraph("Description", band_style)]
    for draw in draws:
        band_row += [Paragraph(esc(draw.get("label") or ""), band_right), ""]
    band_row += [Paragraph("Total", band_right), ""]
    table_data: list[list] = [band_row]

    cost_codes = _union_cost_codes(draws)
    for number, name in cost_codes:
        row: list = [Paragraph(esc(format_cc_number(number, 3)), cat_style),
                     Paragraph(esc(name), wrap_style)]
        row_sum = Decimal("0")
        for draw in draws:
            amt = _category_amount_for_draw(draw, number)
            row += money_cells(amt)
            if amt is not None:
                row_sum += amt
        row += money_cells(row_sum)
        table_data.append(row)

    def totals_row(label, per_draw_value, grand, *, bold):
        lstyle = bold_right if bold else wrap_style
        row = ["", Paragraph(label, lstyle)]
        for draw in draws:
            row += money_cells(per_draw_value(draw), bold=bold)
        row += money_cells(grand, bold=bold)
        return row

    grand_subtotal = sum((_as_decimal(d.get("subtotal", 0)) for d in draws), Decimal("0"))
    table_data.append(totals_row("Subtotal", lambda d: _as_decimal(d.get("subtotal", 0)),
                                 grand_subtotal, bold=True))
    subtotal_idx = len(table_data) - 1

    grand_fee = sum((_as_decimal(d.get("builders_fee", 0)) for d in draws), Decimal("0"))
    fee_row = [Paragraph(format_cc_number(FEE_ITEM_NUMBER, 3), cat_style),
               Paragraph("Builder's Fee", wrap_style)]
    for draw in draws:
        fee_row += money_cells(_as_decimal(draw.get("builders_fee", 0)))
    fee_row += money_cells(grand_fee)
    table_data.append(fee_row)

    grand_total = sum((_as_decimal(d.get("total", 0)) for d in draws), Decimal("0"))
    table_data.append(totals_row("Total Due", lambda d: _as_decimal(d.get("total", 0)),
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
    ]
    # each money group: SPAN its band label across ($, value); hug $ to value
    for g in range(groups):
        dcol = 2 + 2 * g
        vcol = dcol + 1
        style_cmds.append(("SPAN", (dcol, 0), (vcol, 0)))
        style_cmds.append(("RIGHTPADDING", (dcol, 0), (dcol, -1), 0))
        style_cmds.append(("LEFTPADDING", (vcol, 0), (vcol, -1), 0))
        # rule above the money cells on the Subtotal + Total Due rows
        style_cmds.append(("LINEABOVE", (dcol, subtotal_idx), (vcol, subtotal_idx), 0.6, BLACK))
        style_cmds.append(("LINEABOVE", (dcol, total_idx), (vcol, total_idx), 0.6, BLACK))

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        leftMargin=margin, rightMargin=margin,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    doc.build([header_table, Spacer(1, 0.2 * inch), table])
    return buf.getvalue()
