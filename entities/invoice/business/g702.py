"""AIA G702 Application and Certification for Payment — portrait renderer."""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

from entities.invoice.business.cover import _format_money


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _money(value: Any) -> str:
    return _format_money(_dec(value))


def build_g702_lines(grand: dict, retainage_rate: Any = Decimal("0"),
                     original_contract_sum: Any = None) -> dict:
    """Derive the G702 numbered lines (1-9) from the G703 GRAND totals, so the two
    pages reconcile by construction.

    ``grand`` is the second return of ``g703.build_g703_rows`` (keys: scheduled, prev,
    this_period, total_to_date). Per the confirmed model, L1 (Original Contract Sum)
    is the live-Budget contract sum and L2 (Net change by Change Orders) is $0 —
    override L1 via ``original_contract_sum`` once the Budget models a real original
    baseline. Retainage defaults to 0. Identities: L3 = L1+L2; L6 = L4-L5;
    L8 = L6-L7 (= grand This-Period when retainage is 0); L9 = L3-L6.
    """
    l1 = _dec(original_contract_sum if original_contract_sum is not None else grand.get("scheduled"))
    l2 = Decimal("0")
    l3 = l1 + l2
    l4 = _dec(grand.get("total_to_date"))
    rate = _dec(retainage_rate)
    l5 = (l4 * rate).quantize(Decimal("0.01")) if rate else Decimal("0")
    l6 = l4 - l5
    # L7 = prior Certificate's Line 6 = previous cumulative NET OF RETAINAGE (the
    # G703 rows carry gross amounts), so with a constant rate L8 = this_period x
    # (1 - rate). At rate 0 this is just grand.prev and L8 == grand.this_period.
    prev = _dec(grand.get("prev"))
    l7 = prev - (prev * rate).quantize(Decimal("0.01")) if rate else prev
    l8 = l6 - l7
    l9 = l3 - l6
    return {
        "l1_original_contract_sum": l1,
        "l2_net_change_orders": l2,
        "l3_contract_sum_to_date": l3,
        "l4_total_completed_stored": l4,
        "l5_retainage": l5,
        "l6_total_earned_less_retainage": l6,
        "l7_less_previous_certificates": l7,
        "l8_current_payment_due": l8,
        "l9_balance_to_finish": l9,
        "co_additions": Decimal("0"),
        "co_deductions": Decimal("0"),
    }


def build_g702_pdf(header: dict, lines: dict) -> bytes:
    """Portrait letter G702: header band + contractor application ledger + certification."""
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    NAVY = colors.HexColor("#1F3864")
    FONT_SIZE = 8
    LEADING = FONT_SIZE + 2

    def P(text: str, style: ParagraphStyle) -> Paragraph:
        return Paragraph(_html.escape(text) if text else "", style)

    title_style = ParagraphStyle(
        "g702_title",
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        textColor=NAVY,
        alignment=TA_LEFT,
    )
    doc_id_style = ParagraphStyle(
        "g702_doc_id",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=NAVY,
        alignment=TA_RIGHT,
    )
    section_hdr = ParagraphStyle(
        "g702_section",
        fontName="Helvetica-Bold",
        fontSize=FONT_SIZE,
        leading=LEADING,
        textColor=NAVY,
        alignment=TA_LEFT,
    )
    label_style = ParagraphStyle(
        "g702_label",
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=9,
        textColor=NAVY,
        alignment=TA_LEFT,
    )
    body_style = ParagraphStyle(
        "g702_body",
        fontName="Helvetica",
        fontSize=FONT_SIZE,
        leading=LEADING,
        alignment=TA_LEFT,
    )
    small_style = ParagraphStyle(
        "g702_small",
        fontName="Helvetica",
        fontSize=7,
        leading=9,
        alignment=TA_LEFT,
    )
    ledger_label = ParagraphStyle(
        "g702_ledger_l",
        fontName="Helvetica",
        fontSize=FONT_SIZE,
        leading=LEADING,
        alignment=TA_LEFT,
    )
    ledger_money = ParagraphStyle(
        "g702_ledger_r",
        fontName="Helvetica",
        fontSize=FONT_SIZE,
        leading=LEADING,
        alignment=TA_RIGHT,
    )
    bold_ledger_label = ParagraphStyle(
        "g702_ledger_bl",
        fontName="Helvetica-Bold",
        fontSize=FONT_SIZE,
        leading=LEADING,
        alignment=TA_LEFT,
    )
    bold_ledger_money = ParagraphStyle(
        "g702_ledger_br",
        fontName="Helvetica-Bold",
        fontSize=FONT_SIZE,
        leading=LEADING,
        alignment=TA_RIGHT,
    )
    cert_style = ParagraphStyle(
        "g702_cert",
        fontName="Helvetica",
        fontSize=7,
        leading=9,
        alignment=TA_LEFT,
    )

    owner_lines = header.get("owner_lines") or []
    contractor_lines = header.get("contractor_lines") or []
    architect_lines = header.get("architect_lines") or []
    project = header.get("project") or ""
    application_no = header.get("application_no") or ""
    period_to = header.get("period_to") or ""
    contract_for = header.get("contract_for") or ""
    contract_date = header.get("contract_date") or ""

    l1 = _dec(lines.get("l1_original_contract_sum"))
    l2 = _dec(lines.get("l2_net_change_orders"))
    l3 = _dec(lines.get("l3_contract_sum_to_date"))
    l4 = _dec(lines.get("l4_total_completed_stored"))
    l5 = _dec(lines.get("l5_retainage"))
    l6 = _dec(lines.get("l6_total_earned_less_retainage"))
    l7 = _dec(lines.get("l7_less_previous_certificates"))
    l8 = _dec(lines.get("l8_current_payment_due"))
    l9 = _dec(lines.get("l9_balance_to_finish"))
    co_add = _dec(lines.get("co_additions"))
    co_ded = _dec(lines.get("co_deductions"))
    co_net = co_add - co_ded

    margin = 0.5 * inch
    page_w = letter[0]
    usable_w = page_w - 2 * margin

    def _labeled_block(label: str, text_lines: list[str]) -> str:
        body = "<br/>".join(_html.escape(ln) for ln in text_lines if ln is not None)
        return f"<b>{_html.escape(label)}</b><br/>{body}"

    left_hdr_text = "<br/><br/>".join([
        _labeled_block("TO OWNER:", owner_lines),
        _labeled_block("FROM CONTRACTOR:", contractor_lines),
        f"<b>CONTRACT FOR:</b><br/>{_html.escape(contract_for)}",
    ])
    mid_hdr_text = "<br/><br/>".join([
        f"<b>PROJECT:</b><br/>{_html.escape(project)}",
        _labeled_block("VIA ARCHITECT:", architect_lines),
        f"<b>CONTRACT DATE:</b><br/>{_html.escape(contract_date)}",
    ])
    dist_lines = "<br/>".join([
        "Distribution to:",
        "☐ OWNER",
        "☐ ARCHITECT",
        "☐ CONTRACTOR",
    ])
    right_hdr_text = "<br/><br/>".join([
        f"<b>APPLICATION NO:</b><br/>{_html.escape(application_no)}",
        f"<b>PERIOD TO:</b><br/>{_html.escape(period_to)}",
        dist_lines,
    ])
    left_hdr = Paragraph(left_hdr_text, body_style)
    mid_hdr = Paragraph(mid_hdr_text, body_style)
    right_hdr = Paragraph(right_hdr_text, body_style)

    col_w = usable_w / 3.0
    header_band = Table(
        [[left_hdr, mid_hdr, right_hdr]],
        colWidths=[col_w, col_w, col_w],
    )
    header_band.setStyle(
        TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )

    title_row = Table(
        [[P("APPLICATION AND CERTIFICATION FOR PAYMENT", title_style),
          P("AIA DOCUMENT G702", doc_id_style)]],
        colWidths=[usable_w * 0.72, usable_w * 0.28],
    )
    title_row.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )

    ledger_w = usable_w * 0.52
    money_col = 1.05 * inch
    label_col = ledger_w - money_col

    ledger_rows: list[list] = [
        [P("CONTRACTOR'S APPLICATION FOR PAYMENT", section_hdr), ""],
        [
            P("1. ORIGINAL CONTRACT SUM", ledger_label),
            P(_money(l1), ledger_money),
        ],
        [
            P("2. Net change by Change Orders", ledger_label),
            P(_money(l2), ledger_money),
        ],
        [
            P("3. CONTRACT SUM TO DATE (Line 1 +/- 2)", ledger_label),
            P(_money(l3), ledger_money),
        ],
        [
            P("4. TOTAL COMPLETED & STORED TO DATE (Column G on G703)", ledger_label),
            P(_money(l4), ledger_money),
        ],
        [
            P("5. RETAINAGE", ledger_label),
            P(_money(l5), ledger_money),
        ],
        [
            P("6. TOTAL EARNED LESS RETAINAGE (Line 4 less Line 5)", ledger_label),
            P(_money(l6), ledger_money),
        ],
        [
            P("7. LESS PREVIOUS CERTIFICATES FOR PAYMENT (Line 6 from prior Certificate)", ledger_label),
            P(_money(l7), ledger_money),
        ],
        [
            P("8. CURRENT PAYMENT DUE", bold_ledger_label),
            P(_money(l8), bold_ledger_money),
        ],
        [
            P("9. BALANCE TO FINISH INCLUDING RETAINAGE (Line 3 less 6)", ledger_label),
            P(_money(l9), ledger_money),
        ],
    ]

    co_table = Table(
        [
            [P("CHANGE ORDER SUMMARY", section_hdr), "", ""],
            [P("ADDITIONS", label_style), P(_money(co_add), ledger_money), ""],
            [P("DEDUCTIONS", label_style), P(_money(co_ded), ledger_money), ""],
            [P("NET CHANGES", bold_ledger_label), P(_money(co_net), bold_ledger_money), ""],
        ],
        colWidths=[label_col * 0.55, money_col, label_col * 0.45 - money_col],
    )
    co_table.setStyle(
        TableStyle([
            ("SPAN", (0, 0), (-1, 0)),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, NAVY),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )

    ledger = Table(ledger_rows, colWidths=[label_col, money_col])
    ledger.setStyle(
        TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, NAVY),
            ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#CCCCCC")),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )

    contractor_cert = (
        "The undersigned Contractor certifies that to the best of the Contractor's "
        "knowledge, information and belief the Work covered by this Application for "
        "Payment has been completed in accordance with the Contract Documents, that "
        "all amounts have been paid by the Contractor for Work for which previous "
        "Certificates for Payment were issued and payments received from the Owner, "
        "and that current payment shown herein is now due."
    )
    architect_cert = (
        "ARCHITECT'S CERTIFICATE FOR PAYMENT<br/><br/>"
        "In accordance with the Contract Documents, based on on-site observations and "
        "the data comprising this application, the Architect certifies to the Owner "
        "that to the best of the Architect's knowledge, information and belief the "
        "Work has progressed as indicated, the quality of the Work is in accordance "
        "with the Contract Documents, and the Contractor is entitled to payment of "
        "the AMOUNT CERTIFIED."
    )
    notary_block = (
        "<br/>State of ______________ County of ______________<br/>"
        "Sworn before me this _____ day of _________, 20___ "
        "Notary Public _________________________"
    )
    sig_line = "________________________    Date: __________"

    cert_w = usable_w - ledger_w - 0.08 * inch
    right_text = "<br/>".join([
        contractor_cert,
        "<br/><b>CONTRACTOR:</b><br/>By: " + sig_line,
        notary_block,
        architect_cert,
        "<br/><b>AMOUNT CERTIFIED</b><br/>_______________________________________________",
        "<br/><b>ARCHITECT:</b><br/>By: " + sig_line,
    ])
    right_col = Paragraph(right_text, cert_style)

    left_stack = Table(
        [[ledger], [co_table]],
        colWidths=[ledger_w],
    )
    left_stack.setStyle(
        TableStyle([
            ("TOPPADDING", (0, 1), (0, 1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )

    body = Table([[left_stack, right_col]], colWidths=[ledger_w, cert_w])
    body.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )

    footer = P(
        "AIA DOCUMENT G702 - APPLICATION AND CERTIFICATION FOR PAYMENT - 1992 EDITION",
        small_style,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=0.45 * inch,
        bottomMargin=0.4 * inch,
    )
    story = [
        title_row,
        header_band,
        Spacer(1, 0.1 * inch),
        body,
        Spacer(1, 0.15 * inch),
        footer,
    ]
    doc.build(story)
    return buf.getvalue()
