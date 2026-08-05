"""Pure ReportLab renderer for a construction Trend page (per cost-code × draw matrix)."""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any, Optional

from entities.invoice.business.cover import _cost_code_sort_key, _format_money

FEE_ITEM_NUMBER = "90.000"


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
    all its cells are None → renders a blank cell."""
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

    BLUE = colors.HexColor("#1F3864")
    FONT_SIZE = 7

    wrap_style = ParagraphStyle(
        "tr_wrap", fontName="Helvetica", fontSize=FONT_SIZE, leading=FONT_SIZE + 2
    )
    wrap_hdr = ParagraphStyle(
        "tr_wrap_hdr",
        fontName="Helvetica-Bold",
        fontSize=FONT_SIZE,
        leading=FONT_SIZE + 2,
        textColor=BLUE,
    )
    hdr_right = ParagraphStyle(
        "tr_hdr_right",
        fontName="Helvetica-Bold",
        fontSize=FONT_SIZE,
        leading=FONT_SIZE + 2,
        textColor=BLUE,
        alignment=TA_RIGHT,
    )
    bold_right = ParagraphStyle(
        "tr_bold_right",
        fontName="Helvetica-Bold",
        fontSize=FONT_SIZE,
        leading=FONT_SIZE + 2,
        alignment=TA_RIGHT,
    )
    from_style = ParagraphStyle(
        "tr_from", fontName="Helvetica", fontSize=8, leading=10, alignment=TA_LEFT
    )
    to_style = ParagraphStyle(
        "tr_to", fontName="Helvetica", fontSize=8, leading=10, alignment=TA_CENTER
    )

    def W(text: str) -> Paragraph:
        return Paragraph(_html.escape(str(text)) if text else "", wrap_style)

    date_str = header.get("date") or ""
    to_name = header.get("to_name") or ""
    from_lines: list[str] = list(header.get("from_lines") or [])
    to_lines: list[str] = list(header.get("to_lines") or [])

    from_body = "<br/>".join(_html.escape(line) for line in from_lines if line)
    to_parts = [_html.escape("To:")]
    if to_name:
        to_parts.append(f"<b>{_html.escape(to_name)}</b>")
    to_parts.extend(_html.escape(line) for line in to_lines if line)
    to_body = "<br/>".join(to_parts)

    page_size = landscape(letter)
    margin = 0.75 * inch
    usable_w = page_size[0] - 2 * margin

    n_draws = len(draws)
    cat_w = 0.7 * inch  # fits the "Category" header on one line at 7pt
    desc_w = 1.35 * inch
    money_cols = n_draws + 1  # draws + Total; always >= 1
    money_w = max(0.45 * inch, (usable_w - cat_w - desc_w) / money_cols)
    col_widths = [cat_w, desc_w] + [money_w] * money_cols

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=page_size,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    story: list = [
        Paragraph(
            f"Date: {_html.escape(date_str)}",
            ParagraphStyle(
                "tr_date",
                fontName="Helvetica",
                fontSize=8,
                textColor=BLUE,
                alignment=TA_CENTER,
                spaceAfter=8,
            ),
        ),
    ]

    header_table = Table(
        [[Paragraph(from_body, from_style) if from_body else "", Paragraph(to_body, to_style) if to_body else ""]],
        colWidths=[usable_w / 2, usable_w / 2],
    )
    header_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(header_table)
    story.append(
        Paragraph(
            "Trend",
            ParagraphStyle(
                "tr_title",
                fontName="Helvetica",
                fontSize=9,
                textColor=BLUE,
                alignment=TA_CENTER,
                spaceBefore=4,
                spaceAfter=10,
            ),
        )
    )

    header_row: list = [
        Paragraph("Category", wrap_hdr),
        Paragraph("Description", wrap_hdr),
    ]
    for draw in draws:
        header_row.append(Paragraph(_html.escape(str(draw.get("label") or "")), hdr_right))
    header_row.append(Paragraph("Total", hdr_right))

    table_data: list[list] = [header_row]
    cost_codes = _union_cost_codes(draws)

    for number, name in cost_codes:
        row: list = [number, W(name)]
        row_sum = Decimal("0")
        for draw in draws:
            amt = _category_amount_for_draw(draw, number)
            if amt is None:
                row.append("")
            else:
                row.append(_format_money(amt))
                row_sum += amt
        row.append(_format_money(row_sum))
        table_data.append(row)

    table_data.append([""] * len(header_row))
    spacer_idx = len(table_data) - 1

    grand_subtotal = sum((_as_decimal(d.get("subtotal", 0)) for d in draws), Decimal("0"))
    subtotal_row: list = ["", Paragraph("Subtotal", bold_right)]
    for draw in draws:
        subtotal_row.append(Paragraph(_format_money(_as_decimal(draw.get("subtotal", 0))), bold_right))
    subtotal_row.append(Paragraph(_format_money(grand_subtotal), bold_right))
    table_data.append(subtotal_row)
    subtotal_idx = len(table_data) - 1

    grand_fee = sum((_as_decimal(d.get("builders_fee", 0)) for d in draws), Decimal("0"))
    fee_row: list = [FEE_ITEM_NUMBER, W("Builder's Fee")]
    for draw in draws:
        fee_row.append(_format_money(_as_decimal(draw.get("builders_fee", 0))))
    fee_row.append(_format_money(grand_fee))
    table_data.append(fee_row)

    grand_total = sum((_as_decimal(d.get("total", 0)) for d in draws), Decimal("0"))
    total_row: list = ["", Paragraph("Total Due", bold_right)]
    for draw in draws:
        total_row.append(Paragraph(_format_money(_as_decimal(draw.get("total", 0))), bold_right))
    total_row.append(Paragraph(_format_money(grand_total), bold_right))
    table_data.append(total_row)
    total_idx = len(table_data) - 1

    n_rows = len(table_data)
    first_money_col = 2
    last_col = 2 + n_draws

    style_cmds = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), FONT_SIZE),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLUE),
        ("TOPPADDING", (0, 0), (-1, 0), 3),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, BLUE),
        ("FONTNAME", (0, 1), (-1, n_rows - 1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, n_rows - 1), FONT_SIZE),
        ("TOPPADDING", (0, 1), (-1, n_rows - 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, n_rows - 1), 2),
        ("LINEBELOW", (0, 1), (-1, n_rows - 1), 0.25, colors.HexColor("#CCCCCC")),
        ("ALIGN", (first_money_col, 0), (last_col, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, total_idx), (-1, total_idx), "Helvetica-Bold"),
        ("LINEABOVE", (0, total_idx), (-1, total_idx), 0.75, colors.black),
        ("TOPPADDING", (0, total_idx), (-1, total_idx), 4),
        ("BOTTOMPADDING", (0, total_idx), (-1, total_idx), 3),
        ("FONTNAME", (0, subtotal_idx), (-1, subtotal_idx), "Helvetica-Bold"),
        ("LINEABOVE", (0, subtotal_idx), (-1, subtotal_idx), 0.5, colors.HexColor("#888888")),
        ("TOPPADDING", (0, subtotal_idx), (-1, subtotal_idx), 3),
        ("TOPPADDING", (0, spacer_idx), (-1, spacer_idx), 2),
        ("BOTTOMPADDING", (0, spacer_idx), (-1, spacer_idx), 2),
        ("LINEBELOW", (0, spacer_idx), (-1, spacer_idx), 0, colors.white),
    ]

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    doc.build(story)
    return buf.getvalue()
