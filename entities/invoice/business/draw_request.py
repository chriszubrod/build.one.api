"""Pure ReportLab renderer for a construction Draw Request page."""

from __future__ import annotations

import io
from decimal import Decimal

from entities.invoice.business.cover import CoverRollup, _format_money

# The Builder's Fee is schedule-of-values item 90 — it renders as a normal
# category row (Category "90.000" · "Builder's Fee" · amount), NOT as a fee-rate
# label. Constant, not derived from the rate.
FEE_ITEM_NUMBER = "90.000"


def build_draw_request_pdf(header: dict, cover_model: CoverRollup) -> bytes:
    """ReportLab draw request: header blocks + category table + subtotal / fee / total."""
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    BLUE = colors.HexColor("#1F3864")

    wrap_style = ParagraphStyle("dr_wrap", fontName="Helvetica", fontSize=8, leading=10)
    wrap_hdr = ParagraphStyle(
        "dr_wrap_hdr", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=BLUE
    )
    bold_right = ParagraphStyle(
        "dr_bold_right", fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_RIGHT
    )
    hdr_right = ParagraphStyle(
        "dr_hdr_right",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=BLUE,
        alignment=TA_RIGHT,
    )
    from_style = ParagraphStyle(
        "dr_from", fontName="Helvetica", fontSize=9, leading=11, alignment=TA_LEFT
    )
    to_style = ParagraphStyle(
        "dr_to", fontName="Helvetica", fontSize=9, leading=11, alignment=TA_CENTER
    )

    def W(text):
        return Paragraph(_html.escape(str(text)) if text else "", wrap_style)

    date_str = header.get("date") or ""
    draw_number = header.get("draw_number") or ""
    to_name = header.get("to_name") or ""
    from_lines: list[str] = list(header.get("from_lines") or [])
    to_lines: list[str] = list(header.get("to_lines") or [])

    from_body = "<br/>".join(_html.escape(line) for line in from_lines if line)
    to_parts = [_html.escape("To:")]
    if to_name:
        to_parts.append(f"<b>{_html.escape(to_name)}</b>")
    to_parts.extend(_html.escape(line) for line in to_lines if line)
    to_body = "<br/>".join(to_parts)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    story: list = [
        Paragraph(
            f"Date: {_html.escape(date_str)}",
            ParagraphStyle(
                "dr_date",
                fontName="Helvetica",
                fontSize=9,
                textColor=BLUE,
                alignment=TA_CENTER,
                spaceAfter=10,
            ),
        ),
    ]

    header_table = Table(
        [
            [
                Paragraph(from_body, from_style) if from_body else "",
                Paragraph(to_body, to_style) if to_body else "",
            ]
        ],
        colWidths=[3.25 * inch, 3.25 * inch],
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
            f"Draw Request: {_html.escape(draw_number)}",
            ParagraphStyle(
                "dr_draw_num",
                fontName="Helvetica",
                fontSize=9,
                textColor=BLUE,
                alignment=TA_CENTER,
                spaceBefore=4,
                spaceAfter=12,
            ),
        )
    )

    col_widths = [55, 280, 169]
    headers = [
        Paragraph("Category", wrap_hdr),
        Paragraph("Description", wrap_hdr),
        Paragraph("Amount", hdr_right),
    ]

    table_data: list[list] = [headers]
    for cat in cover_model.categories:
        table_data.append([
            cat.cost_code_number,
            W(cat.cost_code_name),
            _format_money(cat.amount),
        ])

    table_data.append(["", "", ""])
    spacer_idx = len(table_data) - 1

    table_data.append([
        "",
        Paragraph("Subtotal", bold_right),
        Paragraph(_format_money(cover_model.subtotal), bold_right),
    ])
    subtotal_idx = len(table_data) - 1
    # Show the fee row when there IS a fee (Decimal 0 is falsy) — gated on the
    # amount, not the rate, so a fee sourced from the persisted invoice delta
    # (no Contract rate) still renders. A genuine 0 fee omits the row.
    if cover_model.builders_fee:
        # Normal category-style row — item 90.000, left-aligned, plain weight
        # (the Amount column's RIGHT align applies to the money string).
        table_data.append([
            FEE_ITEM_NUMBER,
            W("Builder's Fee"),
            _format_money(cover_model.builders_fee),
        ])
    table_data.append([
        "",
        Paragraph("Total Due", bold_right),
        Paragraph(_format_money(cover_model.total), bold_right),
    ])
    total_idx = len(table_data) - 1
    n = len(table_data)

    style_cmds = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLUE),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, BLUE),
        ("FONTNAME", (0, 1), (-1, n - 1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, n - 1), 8),
        ("TOPPADDING", (0, 1), (-1, n - 1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, n - 1), 3),
        ("LINEBELOW", (0, 1), (-1, n - 1), 0.25, colors.HexColor("#CCCCCC")),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, total_idx), (-1, total_idx), "Helvetica-Bold"),
        ("LINEABOVE", (0, total_idx), (-1, total_idx), 0.75, colors.black),
        ("TOPPADDING", (0, total_idx), (-1, total_idx), 5),
        ("BOTTOMPADDING", (0, total_idx), (-1, total_idx), 4),
    ]
    style_cmds.extend([
        ("FONTNAME", (0, subtotal_idx), (-1, subtotal_idx), "Helvetica-Bold"),
        ("LINEABOVE", (0, subtotal_idx), (-1, subtotal_idx), 0.5, colors.HexColor("#888888")),
        ("TOPPADDING", (0, subtotal_idx), (-1, subtotal_idx), 4),
    ])
    style_cmds.extend([
        ("TOPPADDING", (0, spacer_idx), (-1, spacer_idx), 2),
        ("BOTTOMPADDING", (0, spacer_idx), (-1, spacer_idx), 2),
        ("LINEBELOW", (0, spacer_idx), (-1, spacer_idx), 0, colors.white),
    ])

    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    story.append(Spacer(1, 0.35 * inch))
    story.append(
        Paragraph(
            "Please remit payments to Rogers Build, Inc. by bank wire.",
            ParagraphStyle(
                "dr_remit",
                fontName="Helvetica",
                fontSize=9,
                alignment=TA_CENTER,
            ),
        )
    )

    doc.build(story)
    return buf.getvalue()
