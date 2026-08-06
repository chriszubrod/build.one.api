"""AIA G702 Application and Certification for Payment — landscape renderer.

Reproduces the 1992 AIA G702 form (serif type, bordered header grid with the
Owner/Architect/Contractor distribution checkboxes, the 9-line contractor
application ledger with the 5a/5b retainage sub-rows, the notary + architect
certificate blocks, the Change Order Summary table, and the AIA footer) so a
system-generated page reads like the manually produced packets it replaces.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

from entities.invoice.business.cover import _format_money
from entities.invoice.business.packet_render import (
    SERIF,
    SERIF_BOLD,
    SERIF_ITALIC,
    money_number,
)


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
    """Landscape letter G702 (single page) rendered as the 1992 AIA form."""
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    BLACK = colors.black

    def esc(text: Any) -> str:
        return _html.escape(str(text)) if text not in (None, "") else ""

    # ---- paragraph styles -------------------------------------------------
    title_style = ParagraphStyle("g702_title", fontName=SERIF_BOLD, fontSize=13,
                                 leading=15, alignment=TA_LEFT)
    doc_id_style = ParagraphStyle("g702_doc_id", fontName=SERIF_ITALIC, fontSize=11,
                                  leading=13, alignment=TA_RIGHT)
    hdr_label = ParagraphStyle("g702_hl", fontName=SERIF, fontSize=7.5, leading=9.5)
    hdr_value = ParagraphStyle("g702_hv", fontName=SERIF_BOLD, fontSize=7.5, leading=9.5)
    section_hdr = ParagraphStyle("g702_sec", fontName=SERIF_BOLD, fontSize=9, leading=11)
    body = ParagraphStyle("g702_body", fontName=SERIF, fontSize=7.5, leading=9.5)
    ledger_label = ParagraphStyle("g702_ll", fontName=SERIF, fontSize=7.5, leading=9.5)
    ledger_sub = ParagraphStyle("g702_lsub", fontName=SERIF, fontSize=7, leading=8.5,
                                leftIndent=10)
    dollar_style = ParagraphStyle("g702_dol", fontName=SERIF, fontSize=7.5, leading=9.5,
                                  alignment=TA_LEFT)
    value_style = ParagraphStyle("g702_val", fontName=SERIF, fontSize=7.5, leading=9.5,
                                 alignment=TA_RIGHT)
    value_bold = ParagraphStyle("g702_valb", fontName=SERIF_BOLD, fontSize=7.5,
                                leading=9.5, alignment=TA_RIGHT)
    cert = ParagraphStyle("g702_cert", fontName=SERIF, fontSize=7, leading=9)
    cert_bold = ParagraphStyle("g702_certb", fontName=SERIF_BOLD, fontSize=8.5, leading=11)
    footer_style = ParagraphStyle("g702_ft", fontName=SERIF, fontSize=6, leading=7.5)
    footer_bold = ParagraphStyle("g702_ftb", fontName=SERIF_BOLD, fontSize=6.5, leading=8)
    box_label = ParagraphStyle("g702_box", fontName=SERIF, fontSize=7.5, leading=9.5)
    co_hdr = ParagraphStyle("g702_cohdr", fontName=SERIF_BOLD, fontSize=7, leading=9,
                            alignment=TA_CENTER)

    # ---- header data ------------------------------------------------------
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

    margin = 0.45 * inch
    page_w = landscape(letter)[0]
    usable_w = page_w - 2 * margin

    # ---- title ------------------------------------------------------------
    title_row = Table(
        [[Paragraph("APPLICATION AND CERTIFICATION FOR PAYMENT", title_style),
          Paragraph("AIA DOCUMENT G702", doc_id_style)]],
        colWidths=[usable_w * 0.62, usable_w * 0.38],
    )
    title_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, 0), (-1, -1), 1.6, BLACK),
    ]))

    def stacked(*blocks: str) -> Paragraph:
        return Paragraph("<br/>".join(b for b in blocks if b), body)

    def labeled(label: str, text_lines: list[str]) -> str:
        inner = "<br/>".join(f"<b>{esc(ln)}</b>" for ln in text_lines if ln)
        return f"{esc(label)}<br/>{inner}" if inner else esc(label)

    # ---- header grid (4 columns, bordered) --------------------------------
    owner_cell = stacked(
        labeled("TO OWNER:", owner_lines),
        "",
        labeled("FROM CONTRACTOR:", contractor_lines),
        "",
        f"<i>CONTRACT FOR:</i>  <b>{esc(contract_for)}</b>",
    )
    project_cell = stacked(
        f"PROJECT:  <b>{esc(project)}</b>",
        "",
        labeled("VIA ARCHITECT:", architect_lines),
        "",
        f"CONTRACT DATE:  <b>{esc(contract_date)}</b>",
    )
    appno_cell = stacked(
        f"APPLICATION NO:  <b>{esc(application_no)}</b>",
        "",
        f"PERIOD TO:  <b>{esc(period_to)}</b>",
    )

    # Distribution checkbox mini-grid — real bordered squares (Times has no ballot
    # glyph), an X marking the Contractor row.
    def checkbox(marked: bool) -> Table:
        t = Table([[("X" if marked else "")]], colWidths=[9], rowHeights=[9])
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, BLACK),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTNAME", (0, 0), (-1, -1), SERIF_BOLD),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return t

    dist_grid = Table(
        [
            [Paragraph("Distribution to:", box_label), ""],
            [checkbox(False), Paragraph("OWNER", box_label)],
            [checkbox(False), Paragraph("ARCHITECT", box_label)],
            [checkbox(True), Paragraph("CONTRACTOR", box_label)],
        ],
        colWidths=[0.24 * inch, 1.0 * inch],
    )
    dist_grid.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    header_grid = Table(
        [[owner_cell, project_cell, appno_cell, dist_grid]],
        colWidths=[usable_w * 0.34, usable_w * 0.34, usable_w * 0.18, usable_w * 0.14],
    )
    header_grid.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.0, BLACK),
        ("LINEAFTER", (0, 0), (-2, -1), 0.5, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    # ---- left column: application ledger ----------------------------------
    intro = Paragraph(
        "Application is made for payment, as shown below, in connection with the "
        "Contract. Continuation Sheet, AIA Document G703, is attached.", body)

    def lrow(label: str, value: Decimal, style=ledger_label, vstyle=value_style):
        return [Paragraph(label, style), Paragraph("$", dollar_style),
                Paragraph(money_number(value), vstyle)]

    ledger_rows = [
        [Paragraph("CONTRACTOR'S APPLICATION FOR PAYMENT", section_hdr), "", ""],
        [intro, "", ""],
        lrow("1. ORIGINAL CONTRACT SUM", l1),
        lrow("2. Net change by Change Orders (Incl in line 1)", l2),
        lrow("3. CONTRACT SUM TO DATE (Line 1 &#177; 2)", l3),
        lrow("4. TOTAL COMPLETED &amp; STORED TO DATE (Column G on G703)", l4),
        [Paragraph("5. RETAINAGE:", ledger_label), "", ""],
        [Paragraph("a. _____ % of Completed Work (Column D + E on G703)", ledger_sub),
         Paragraph("$", dollar_style), Paragraph(money_number(l5), value_style)],
        [Paragraph("b. _____ % of Stored Material (Column F on G703)", ledger_sub),
         Paragraph("$", dollar_style), Paragraph("n/a", value_style)],
        [Paragraph("Total Retainage (Lines 5a + 5b or Total in Column I of G703)",
                   ledger_sub), Paragraph("$", dollar_style),
         Paragraph(money_number(l5), value_style)],
        lrow("6. TOTAL EARNED LESS RETAINAGE (Line 4 Less Line 5 Total)", l6),
        lrow("7. LESS PREVIOUS CERTIFICATES FOR PAYMENT (Line 6 from prior Certificate)", l7),
        lrow("8. CURRENT PAYMENT DUE", l8, style=ledger_label, vstyle=value_bold),
        lrow("9. BALANCE TO FINISH, INCLUDING RETAINAGE (Line 3 less 6)", l9),
    ]

    ledger_w = usable_w * 0.52
    dollar_w = 0.16 * inch
    value_w = 0.95 * inch
    label_w = ledger_w - dollar_w - value_w
    # rows whose value cell carries the ruled-underline ledger look
    ruled = {2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13}
    ledger = Table(ledger_rows, colWidths=[label_w, dollar_w, value_w])
    ledger_style = [
        ("SPAN", (0, 0), (2, 0)),
        ("SPAN", (0, 1), (2, 1)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]
    for r in ruled:
        ledger_style.append(("LINEBELOW", (2, r), (2, r), 0.5, BLACK))
    ledger.setStyle(TableStyle(ledger_style))

    # Change Order Summary — bordered table
    def co_val(v: Decimal) -> Paragraph:
        return Paragraph(_money(v), value_style)

    co_rows = [
        [Paragraph("CHANGE ORDER SUMMARY", co_hdr), Paragraph("ADDITIONS", co_hdr),
         Paragraph("DEDUCTIONS", co_hdr)],
        [Paragraph("Total changes approved in previous months by Owner", box_label),
         co_val(Decimal("0")), co_val(Decimal("0"))],
        [Paragraph("Total approved this Month", box_label),
         co_val(co_add), co_val(co_ded)],
        [Paragraph("TOTALS", cert_bold), co_val(co_add), co_val(co_ded)],
        [Paragraph("NET CHANGES by Change Order", box_label), co_val(co_net), ""],
    ]
    co_table = Table(co_rows, colWidths=[ledger_w - 2 * 1.05 * inch, 1.05 * inch, 1.05 * inch])
    co_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, BLACK),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BLACK),
        ("SPAN", (1, 4), (2, 4)),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (1, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("FONTNAME", (0, 0), (-1, -1), SERIF),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
    ]))

    left_col = Table([[ledger], [Spacer(1, 8)], [co_table]], colWidths=[ledger_w])
    left_col.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    # ---- right column: certifications -------------------------------------
    contractor_cert = Paragraph(
        "The undersigned Contractor certifies that to the best of the Contractor's "
        "knowledge, information and belief the Work covered by this Application for "
        "Payment has been completed in accordance with the Contract Documents, that "
        "all amounts have been paid by the Contractor for Work for which previous "
        "Certificates for Payment were issued and payments received from the Owner, "
        "and that current payment shown herein is now due.", cert)
    notary = Paragraph(
        "<b>CONTRACTOR:</b><br/><br/>"
        "By: ______________________________   Date: ______________<br/><br/>"
        "Subscribed and sworn to before me this ______ day of __________, 20____<br/>"
        "County of: ______________   State of: ______________<br/>"
        "Notary Public: ______________________________<br/>"
        "My Commission expires: ______________", cert)
    architect_cert = Paragraph(
        "In accordance with the Contract Documents, based on on-site observations and "
        "the data comprising this application, the Architect certifies to the Owner "
        "that to the best of the Architect's knowledge, information and belief the "
        "Work has progressed as indicated, the quality of the Work is in accordance "
        "with the Contract Documents, and the Contractor is entitled to payment of "
        "the AMOUNT CERTIFIED.", cert)
    amount_certified = Paragraph(
        "AMOUNT CERTIFIED . . . . . . . . . . $ ______________________<br/>"
        "<i>(Attach explanation if amount certified differs from the amount applied. "
        "Initial all figures on this Application and on the Continuation Sheet that "
        "are changed to conform with the amount certified.)</i><br/><br/>"
        "<b>ARCHITECT:</b><br/>"
        "By: ______________________________   Date: ______________", cert)
    not_negotiable = Paragraph(
        "This Certificate is not negotiable. The AMOUNT CERTIFIED is payable only to "
        "the Contractor named herein. Issuance, payment and acceptance of payment are "
        "without prejudice to any rights of the Owner or Contractor under this "
        "Contract.", cert)

    cert_w = usable_w - ledger_w
    right_rows = [
        [contractor_cert],
        [Spacer(1, 6)],
        [notary],
        [Spacer(1, 4)],
        [Paragraph("ARCHITECT'S CERTIFICATE FOR PAYMENT", cert_bold)],
        [architect_cert],
        [Spacer(1, 4)],
        [amount_certified],
        [Spacer(1, 6)],
        [not_negotiable],
    ]
    right_col = Table(right_rows, colWidths=[cert_w])
    right_col.setStyle(TableStyle([
        ("LINEABOVE", (0, 4), (-1, 4), 0.75, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 4), (-1, 4), 4),
    ]))

    body_tbl = Table([[left_col, right_col]], colWidths=[ledger_w, cert_w])
    body_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 12),
        ("LEFTPADDING", (1, 0), (1, 0), 12),
        ("RIGHTPADDING", (1, 0), (-1, -1), 0),
        ("LINEBEFORE", (1, 0), (1, 0), 0.5, BLACK),
    ]))

    footer = Table(
        [[Paragraph(
            "AIA DOCUMENT G702 &#183; APPLICATION AND CERTIFICATION FOR PAYMENT &#183; "
            "1992 EDITION &#183; AIA &#183; &#169;1992 &#183; THE AMERICAN INSTITUTE OF "
            "ARCHITECTS, 1735 NEW YORK AVE., N.W., WASHINGTON, DC 20006-5292", footer_style)],
         [Paragraph(
            "Users may obtain validation of this document by requesting a completed AIA "
            "Document D401 - Certification of Document's Authenticity from the Licensee.",
            footer_bold)]],
        colWidths=[usable_w],
    )
    footer.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, BLACK),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, 0), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        leftMargin=margin, rightMargin=margin,
        topMargin=0.35 * inch, bottomMargin=0.3 * inch,
    )
    doc.build([
        title_row,
        header_grid,
        Spacer(1, 8),
        body_tbl,
        Spacer(1, 8),
        footer,
    ])
    return buf.getvalue()
