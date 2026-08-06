"""AIA G703 Continuation Sheet — assembler (Budget SoV x draws) + landscape renderer.

The renderer reproduces the AIA G703 grid (serif type, full cell borders, the A-I
column-letter row with the WORK COMPLETED span over columns D+E, 4-decimal item
numbers, and the bold GRAND TOTALS row + AIA validation footer) so a
system-generated continuation sheet reads like the manual packets it replaces.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any, Optional

from entities.invoice.business.cover import _cost_code_sort_key, _format_money
from entities.invoice.business.packet_render import (
    SERIF,
    SERIF_BOLD,
    SERIF_ITALIC,
    format_cc_number,
)

# Schedule-of-values line number for the Builder's Fee. It is driven by each draw's
# COMPUTED fee (draw-financials), not by a work cost-code category, so it's split out
# of the work rows and rebuilt as a synthetic line.
FEE_ITEM_NUMBER = "90"


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _money(value: Any) -> str:
    return _format_money(_dec(value))


def _fmt_pct(total_to_date: Decimal, scheduled: Decimal) -> str:
    """% complete (G/C). $0 scheduled → '0.00%' (matches the AIA form for $0-SoV lines)."""
    if scheduled == 0:
        return "0.00%"
    return f"{(total_to_date / scheduled * Decimal('100')).quantize(Decimal('0.01'))}%"


def _work_amounts(draw: dict) -> dict:
    """This draw's WORK amount per cost-code number (summing category cells). The fee
    is NOT here — build_cover_rollup excludes the Manual fee line — so it never
    double-counts against the synthetic fee line 90."""
    m: dict = {}
    for c in draw.get("categories") or []:
        num = str(c.get("cost_code_number") or "")
        m[num] = m.get(num, Decimal("0")) + _dec(c.get("amount"))
    return m


def build_g703_rows(sov: list, draws: list, current_label: str,
                    fee_item_number: str = FEE_ITEM_NUMBER):
    """Assemble G703 (rows, grand) from the live Budget schedule of values + the
    draw-financials.

    ``sov``: [{cost_code_number, cost_code_name, scheduled_value}] — the budget SoV
    (col C). ``draws``: the project's coded draws (ordered), each
    {label, categories, subtotal, builders_fee, total}. ``current_label``: the invoice
    being packeted — splits prior draws (col D, From Previous) from this one (col E,
    This Period). Row set = SoV cost codes UNION any draw cost code not in the budget
    (over-budget → C=$0, negative Balance). The Builder's Fee line
    (``fee_item_number``) is a synthetic row: C from the budget fee line, D = sum of
    prior draws' computed fees, E = the current draw's fee.
    """
    labels = [d.get("label") for d in draws]
    cur_idx = labels.index(current_label) if current_label in labels else len(draws)
    prior = draws[:cur_idx]
    current: Optional[dict] = draws[cur_idx] if cur_idx < len(draws) else None

    names: dict = {}
    scheduled: dict = {}
    for s in sov:
        num = str(s.get("cost_code_number") or "")
        scheduled[num] = _dec(s.get("scheduled_value"))
        if s.get("cost_code_name") and num not in names:
            names[num] = s.get("cost_code_name")

    def _absorb_names(draw):
        for c in draw.get("categories") or []:
            num = str(c.get("cost_code_number") or "")
            if num and c.get("cost_code_name") and num not in names:
                names[num] = c.get("cost_code_name")

    # Name collection doesn't need the prior/current split (only the D/E amounts do).
    for d in prior + ([current] if current else []):
        _absorb_names(d)

    prev: dict = {}
    for d in prior:
        for num, amt in _work_amounts(d).items():
            prev[num] = prev.get(num, Decimal("0")) + amt
    cur: dict = _work_amounts(current) if current else {}

    # Identify the budget's Builder's Fee schedule-of-values line — the conventional
    # item number (exact, OR the integer part so 90 / 90.0 / 90.01 all resolve) OR the
    # "Builder's Fee" name. It drives fee_C and is EXCLUDED from the work rows: a
    # draw's fee is carried separately (draw-financials builders_fee), so the fee line
    # must never ALSO render as a work code (which would double-count + phantom it).
    fee_number = next(
        (num for num in scheduled
         if num == fee_item_number
         or num.split(".")[0] == fee_item_number
         or (names.get(num) or "").strip().lower() == "builder's fee"),
        None,
    )

    # Work rows = union of SoV / prior / current cost codes, minus the fee line.
    work_codes = (set(scheduled) | set(prev) | set(cur)) - ({fee_number} if fee_number else set())

    def _make_row(num, C, D, E, name):
        G = D + E
        return {
            "item_no": num, "description": name or "",
            "scheduled": C, "prev": D, "this_period": E, "stored": Decimal("0"),
            "total_to_date": G, "pct": _fmt_pct(G, C), "balance": C - G,
            "retainage": Decimal("0"),
        }

    rows = [
        _make_row(num, scheduled.get(num, Decimal("0")), prev.get(num, Decimal("0")),
                  cur.get(num, Decimal("0")), names.get(num, ""))
        for num in sorted(work_codes, key=_cost_code_sort_key)
    ]

    # Synthetic Builder's Fee line: C from the budget fee line (if any), D = sum of
    # prior draws' computed fees, E = the current draw's fee.
    fee_C = scheduled.get(fee_number, Decimal("0")) if fee_number else Decimal("0")
    fee_D = sum((_dec(d.get("builders_fee")) for d in prior), Decimal("0"))
    fee_E = _dec(current.get("builders_fee")) if current else Decimal("0")
    rows.append(_make_row(fee_number or fee_item_number, fee_C, fee_D, fee_E,
                          names.get(fee_number, "Builder's Fee")))

    grand_scheduled = sum((r["scheduled"] for r in rows), Decimal("0"))
    grand_prev = sum((r["prev"] for r in rows), Decimal("0"))
    grand_this = sum((r["this_period"] for r in rows), Decimal("0"))
    grand_total = grand_prev + grand_this
    grand = {
        "scheduled": grand_scheduled, "prev": grand_prev, "this_period": grand_this,
        "stored": Decimal("0"), "total_to_date": grand_total,
        "pct": _fmt_pct(grand_total, grand_scheduled),
        "balance": grand_scheduled - grand_total, "retainage": Decimal("0"),
    }
    return rows, grand


def build_g703_pdf(header: dict, rows: list[dict], grand: dict) -> bytes:
    """Landscape letter G703 rendered as the AIA continuation sheet grid."""
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    BLACK = colors.black
    FONT_SIZE = 6.5  # serif; fits the full schedule of values in two pages
    LEADING = FONT_SIZE + 1

    def P(text: str, style: ParagraphStyle) -> Paragraph:
        return Paragraph(_html.escape(text) if text else "", style)

    title_style = ParagraphStyle("g703_title", fontName=SERIF_BOLD, fontSize=13,
                                 leading=15, alignment=TA_LEFT)
    doc_id_style = ParagraphStyle("g703_doc_id", fontName=SERIF_ITALIC, fontSize=11,
                                  leading=13, alignment=TA_RIGHT)
    static_style = ParagraphStyle("g703_static", fontName=SERIF, fontSize=7, leading=8.5,
                                  alignment=TA_LEFT)
    hdr_label_style = ParagraphStyle("g703_hdr_label", fontName=SERIF_BOLD, fontSize=7,
                                     leading=8.5, alignment=TA_RIGHT)
    hdr_value_style = ParagraphStyle("g703_hdr_value", fontName=SERIF, fontSize=7,
                                     leading=8.5, alignment=TA_LEFT)
    wrap_style = ParagraphStyle("g703_wrap", fontName=SERIF, fontSize=FONT_SIZE,
                                leading=LEADING, alignment=TA_LEFT)
    letter_style = ParagraphStyle("g703_letter", fontName=SERIF, fontSize=FONT_SIZE,
                                  leading=LEADING, alignment=TA_CENTER)
    hdr_center = ParagraphStyle("g703_hdr_c", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=LEADING, alignment=TA_CENTER)
    money_style = ParagraphStyle("g703_money", fontName=SERIF, fontSize=FONT_SIZE,
                                 leading=LEADING, alignment=TA_RIGHT)
    bold_right = ParagraphStyle("g703_bold_r", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=LEADING, alignment=TA_RIGHT)
    bold_left = ParagraphStyle("g703_bold_l", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                               leading=LEADING, alignment=TA_LEFT)
    footer_bold = ParagraphStyle("g703_ftb", fontName=SERIF_BOLD, fontSize=7,
                                 leading=9, alignment=TA_LEFT)

    application_no = header.get("application_no") or ""
    application_date = header.get("application_date") or ""
    period_to = header.get("period_to") or ""
    architect_project_no = header.get("architect_project_no") or ""

    page_size = landscape(letter)
    margin = 0.4 * inch
    usable_w = page_size[0] - 2 * margin

    # Column widths: A item, B description, then 8 numeric columns (C D E F G % H I).
    item_w = 0.5 * inch
    desc_w = 1.7 * inch
    money_w = (usable_w - item_w - desc_w) / 8.0
    col_widths = [item_w, desc_w] + [money_w] * 8

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=page_size,
        leftMargin=margin, rightMargin=margin,
        topMargin=0.35 * inch, bottomMargin=0.35 * inch,
    )

    title_row = Table(
        [[P("CONTINUATION SHEET", title_style), P("AIA DOCUMENT G703", doc_id_style)]],
        colWidths=[3.2 * inch, usable_w - 3.2 * inch],
    )
    title_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, 0), (-1, -1), 1.6, BLACK),
    ]))

    static_block = "<br/>".join([
        "AIA Document G702, APPLICATION AND CERTIFICATION FOR PAYMENT, containing",
        "Contractor's signed certification is attached.",
        "In tabulations below, amounts are stated to the nearest dollar.",
        "Use Column I on Contracts where variable retainage for line items may apply.",
    ])

    header_fields = Table(
        [
            [P("APPLICATION NO:", hdr_label_style), P(application_no, hdr_value_style)],
            [P("APPLICATION DATE:", hdr_label_style), P(application_date, hdr_value_style)],
            [P("PERIOD TO:", hdr_label_style), P(period_to, hdr_value_style)],
            [P("ARCHITECT'S PROJECT NO:", hdr_label_style), P(architect_project_no, hdr_value_style)],
        ],
        colWidths=[1.5 * inch, 1.1 * inch],
    )
    header_fields.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    intro = Table(
        [[Paragraph(static_block, static_style), header_fields]],
        colWidths=[usable_w - 2.6 * inch, 2.6 * inch],
    )
    intro.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))

    # 3-row grid header: letter row, then a labels row with WORK COMPLETED spanning
    # D+E, then a sub-labels row for FROM PREVIOUS / THIS PERIOD.
    div = "&#247;"  # ÷
    letter_row = [P(x, letter_style) for x in ["A", "B", "C", "D", "E", "F", "G", "", "H", "I"]]
    label_row = [
        P("ITEM NO", hdr_center),
        P("DESCRIPTION OF WORK", hdr_center),
        P("SCHEDULED VALUE", hdr_center),
        P("WORK COMPLETED", hdr_center),
        "",
        P("MATERIALS PRESENTLY STORED (NOT IN D OR E)", hdr_center),
        P("TOTAL COMPLETED AND STORED TO DATE (D+E+F)", hdr_center),
        Paragraph(f"% (G{div}C)", hdr_center),
        P("BALANCE TO FINISH (C-G)", hdr_center),
        P("RETAINAGE (IF VARIABLE RATE)", hdr_center),
    ]
    sub_row = [
        "", "", "",
        P("FROM PREVIOUS APPLICATION (D+E)", hdr_center),
        P("THIS PERIOD", hdr_center),
        "", "", "", "", "",
    ]

    table_data: list[list] = [letter_row, label_row, sub_row]

    for row in rows:
        table_data.append([
            P(format_cc_number(row.get("item_no"), 4), wrap_style),
            P(row.get("description") or "", wrap_style),
            P(_money(row["scheduled"]), money_style),
            P(_money(row["prev"]), money_style),
            P(_money(row["this_period"]), money_style),
            P(_money(row["stored"]), money_style),
            P(_money(row["total_to_date"]), money_style),
            P(row.get("pct") or "", money_style),
            P(_money(row["balance"]), money_style),
            P(_money(row["retainage"]), money_style),
        ])

    grand_idx = len(table_data)
    table_data.append([
        "",
        P("GRAND TOTALS", bold_left),
        P(_money(grand["scheduled"]), bold_right),
        P(_money(grand["prev"]), bold_right),
        P(_money(grand["this_period"]), bold_right),
        P(_money(grand["stored"]), bold_right),
        P(_money(grand["total_to_date"]), bold_right),
        P(grand.get("pct") or "", bold_right),
        P(_money(grand["balance"]), bold_right),
        P(_money(grand["retainage"]), bold_right),
    ])

    style_cmds = [
        # Full cell borders on the entire grid (header + body + grand).
        ("GRID", (0, 0), (-1, -1), 0.5, BLACK),
        # header rows 0-2
        ("SPAN", (3, 1), (4, 1)),        # WORK COMPLETED spans D+E
        ("SPAN", (0, 1), (0, 2)),        # ITEM NO
        ("SPAN", (1, 1), (1, 2)),        # DESCRIPTION
        ("SPAN", (2, 1), (2, 2)),        # SCHEDULED VALUE
        ("SPAN", (5, 1), (5, 2)),        # MATERIALS
        ("SPAN", (6, 1), (6, 2)),        # TOTAL
        ("SPAN", (7, 1), (7, 2)),        # %
        ("SPAN", (8, 1), (8, 2)),        # BALANCE
        ("SPAN", (9, 1), (9, 2)),        # RETAINAGE
        ("VALIGN", (0, 0), (-1, 2), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 2), 1),
        ("BOTTOMPADDING", (0, 0), (-1, 2), 1),
        # body
        ("VALIGN", (0, 3), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 3), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 3), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        # grand totals row
        ("TOPPADDING", (0, grand_idx), (-1, grand_idx), 3),
        ("BOTTOMPADDING", (0, grand_idx), (-1, grand_idx), 3),
    ]

    main_table = LongTable(table_data, colWidths=col_widths, repeatRows=3)
    main_table.setStyle(TableStyle(style_cmds))

    footer = P(
        "Users may obtain validation of this document by requesting a completed AIA "
        "Document D401 - Certification of Document's Authenticity from the Licensee.",
        footer_bold,
    )

    story = [title_row, intro, Spacer(1, 0.1 * inch), main_table, Spacer(1, 6), footer]
    doc.build(story)
    return buf.getvalue()
