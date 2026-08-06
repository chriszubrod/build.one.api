"""Pure ReportLab renderer for a construction Draw Request (Invoice) page.

Styled to match the manually produced packets: serif type, the Rogers Build mark
top-right, a gray column-header band, 3-decimal cost-code (Category) numbers, and a
split ``$ | amount`` money column.
"""

from __future__ import annotations

import io
from decimal import Decimal

from entities.invoice.business.cover import CoverRollup
from entities.invoice.business.packet_render import (
    BAND_FILL,
    SERIF,
    SERIF_BOLD,
    format_cc_number,
    logo_flowable,
    money_number,
)

# The Builder's Fee renders as schedule-of-values item 90 — formatted like every
# other Category number (3 decimals → "90.000"). Constant, not derived from the rate.
FEE_ITEM_NUMBER = "90"


def build_draw_request_pdf(header: dict, cover_model: CoverRollup) -> bytes:
    """ReportLab draw request: two-party header + logo, category table, subtotal /
    Builder's Fee / total."""
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    BLACK = colors.black
    BAND = colors.HexColor(BAND_FILL)

    def esc(text) -> str:
        return _html.escape(str(text)) if text not in (None, "") else ""

    date_style = ParagraphStyle("dr_date", fontName=SERIF, fontSize=10, leading=13,
                                alignment=TA_CENTER)
    from_style = ParagraphStyle("dr_from", fontName=SERIF, fontSize=9.5, leading=12,
                                alignment=TA_LEFT)
    to_style = ParagraphStyle("dr_to", fontName=SERIF, fontSize=9.5, leading=12,
                              alignment=TA_LEFT)
    band_style = ParagraphStyle("dr_band", fontName=SERIF_BOLD, fontSize=10, leading=12)
    band_right = ParagraphStyle("dr_band_r", fontName=SERIF_BOLD, fontSize=10,
                                leading=12, alignment=TA_RIGHT)
    cat_style = ParagraphStyle("dr_cat", fontName=SERIF, fontSize=9.5, leading=12,
                               alignment=TA_RIGHT)
    desc_style = ParagraphStyle("dr_desc", fontName=SERIF, fontSize=9.5, leading=12)
    money_style = ParagraphStyle("dr_money", fontName=SERIF, fontSize=9.5, leading=12,
                                 alignment=TA_RIGHT)
    dollar_style = ParagraphStyle("dr_dollar", fontName=SERIF, fontSize=9.5, leading=12)
    bold_right = ParagraphStyle("dr_bold_r", fontName=SERIF_BOLD, fontSize=9.5,
                                leading=12, alignment=TA_RIGHT)
    bold_dollar = ParagraphStyle("dr_bold_d", fontName=SERIF_BOLD, fontSize=9.5, leading=12)
    remit_style = ParagraphStyle("dr_remit", fontName=SERIF, fontSize=10, leading=13,
                                 alignment=TA_CENTER)

    date_str = header.get("date") or ""
    draw_number = header.get("draw_number") or ""
    to_name = header.get("to_name") or ""
    from_lines = list(header.get("from_lines") or [])
    to_lines = list(header.get("to_lines") or [])

    margin = 0.75 * inch
    usable_w = letter[0] - 2 * margin

    # ---- header: Date over From/To, logo top-right ------------------------
    from_body = "<br/>".join(esc(ln) for ln in from_lines if ln)
    to_parts = [f"To:&nbsp;&nbsp;{esc(to_name)}"] if to_name else ["To:"]
    to_parts.extend(esc(ln) for ln in to_lines if ln)
    if draw_number:
        to_parts.append("")
        to_parts.append(f"Draw Request:&nbsp;&nbsp;{esc(draw_number)}")
    to_body = "<br/>".join(to_parts)

    left_w = usable_w * 0.76
    logo = logo_flowable(usable_w * 0.22, 1.05 * inch)

    from_to = Table(
        [[Paragraph(from_body, from_style), Paragraph(to_body, to_style)]],
        colWidths=[left_w * 0.48, left_w * 0.52],
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
        ("TOPPADDING", (0, 0), (0, 0), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 10),
    ]))
    header_table = Table(
        [[left_stack, logo or ""]], colWidths=[left_w, usable_w - left_w],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "TOP"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))

    # ---- category table ---------------------------------------------------
    cat_w = 1.05 * inch  # wide enough for the "CATEGORY" band label on one line
    dollar_w = 0.22 * inch
    amount_w = 1.25 * inch
    desc_w = usable_w - cat_w - dollar_w - amount_w

    def money_row(category, description, value, *, bold=False):
        vstyle = bold_right if bold else money_style
        dsty = (bold_dollar if bold else dollar_style)
        return [
            Paragraph(esc(category), cat_style) if category else "",
            Paragraph(description, desc_style) if not isinstance(description, Paragraph) else description,
            Paragraph("$", dsty),
            Paragraph(money_number(value), vstyle),
        ]

    band_row = [
        Paragraph("CATEGORY", band_style),
        Paragraph("DESCRIPTION", band_style),
        Paragraph("AMOUNT", band_right),
        "",
    ]
    table_data: list[list] = [band_row]

    for cat in cover_model.categories:
        table_data.append(money_row(
            format_cc_number(cat.cost_code_number, 3), esc(cat.cost_code_name), cat.amount))

    subtotal_idx = len(table_data)
    table_data.append(money_row(
        "", Paragraph("Subtotal", bold_right), cover_model.subtotal, bold=True))

    if cover_model.builders_fee:
        table_data.append(money_row(
            format_cc_number(FEE_ITEM_NUMBER, 3), esc("Builder's Fee"), cover_model.builders_fee))

    total_idx = len(table_data)
    table_data.append(money_row(
        "", Paragraph("Total Due", bold_right), cover_model.total, bold=True))

    table = Table(table_data, colWidths=[cat_w, desc_w, dollar_w, amount_w])
    style_cmds = [
        # gray column-header band
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("SPAN", (2, 0), (3, 0)),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        # body spacing (generous, like the manual packet)
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # $ column hugs its number
        ("RIGHTPADDING", (2, 0), (2, -1), 0),
        ("LEFTPADDING", (3, 0), (3, -1), 0),
        # subtotal + total: rule above the money cells
        ("LINEABOVE", (2, subtotal_idx), (3, subtotal_idx), 0.75, BLACK),
        ("LINEABOVE", (2, total_idx), (3, total_idx), 0.75, BLACK),
    ]
    table.setStyle(TableStyle(style_cmds))

    remit = Paragraph("~~Please remit payments to Rogers Build, Inc. by bank wire.~~",
                      remit_style)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=margin, rightMargin=margin,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
    )
    doc.build([header_table, Spacer(1, 0.2 * inch), table, Spacer(1, 0.4 * inch), remit])
    return buf.getvalue()
