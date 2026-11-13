"""Application for Payment — the draw packet's summary page (U-792).

Our own document, styled like the Draw Request (serif type, the Rogers Build mark, a
gray band, a split ``$ | amount`` money column) — not a reproduction of any published
industry form. It carries the figures an owner needs to approve a draw: the contract
sum, the work completed to date, what earlier applications billed, the amount due
now and the balance to finish. The totals come from the Schedule of Values grand
totals, so the two pages reconcile by construction.

The page is landscape (U-792a): the figures on the left and, stacked beside them,
up to four optional blocks, each switched per project
(``entities/project_draw_packet_setting``): the contractor's statement, a notary
block, an owner approval line and an architect review. With every block off the
figures take the full width.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any, Optional

from entities.invoice.business.packet_render import (
    BAND_FILL,
    SERIF,
    SERIF_BOLD,
    SERIF_ITALIC,
    application_header_fields,
    dec as _dec,
    document_header,
    landscape_doc,
    money_number,
)
from shared.api.money import to_decimal_or_none as _dec_or_none


def build_application_lines(grand: dict, retainage_rate: Any = Decimal("0"),
                            original_contract_sum: Any = None) -> dict:
    """The Application for Payment figures, derived from the Schedule of Values GRAND
    totals so the two pages reconcile by construction.

    ``grand`` is the second return of ``schedule_of_values.build_sov_rows`` (keys:
    scheduled, prev, this_period, total_to_date). ``contract_sum`` is the live-Budget
    schedule total and ``net_change_orders`` is $0 — override the contract sum via
    ``original_contract_sum`` once the Budget models a real original baseline.
    Retainage defaults to 0. Identities: contract_sum_to_date = contract_sum +
    net_change_orders; earned_less_retainage = completed_to_date - retainage;
    amount_due = earned_less_retainage - previous_applications (= grand this_period
    when retainage is 0); balance_to_finish = contract_sum_to_date -
    earned_less_retainage.

    No contract sum (U-791): when ``grand["scheduled"]`` is None (the project has no
    schedule) and no ``original_contract_sum`` is given, contract_sum,
    contract_sum_to_date and balance_to_finish are None and render blank — a $0
    contract would print a negative balance to finish. The payment figures are
    unaffected.
    """
    change_orders = Decimal("0")
    contract = original_contract_sum if original_contract_sum is not None else grand.get("scheduled")
    contract_sum = _dec_or_none(contract)
    contract_to_date = contract_sum + change_orders if contract_sum is not None else None
    completed = _dec(grand.get("total_to_date"))
    rate = _dec(retainage_rate)
    retainage = (completed * rate).quantize(Decimal("0.01")) if rate else Decimal("0")
    earned = completed - retainage
    # Previous applications are carried NET OF RETAINAGE (the schedule rows carry gross
    # amounts), so with a constant rate amount_due = this_period x (1 - rate). At rate
    # 0 this is just grand.prev and amount_due == grand.this_period.
    prev = _dec(grand.get("prev"))
    previous = prev - (prev * rate).quantize(Decimal("0.01")) if rate else prev
    return {
        "contract_sum": contract_sum,
        "net_change_orders": change_orders,
        "contract_sum_to_date": contract_to_date,
        "completed_to_date": completed,
        "retainage": retainage,
        "earned_less_retainage": earned,
        "previous_applications": previous,
        "amount_due": earned - previous,
        "balance_to_finish": contract_to_date - earned if contract_to_date is not None else None,
    }


def build_application_pdf(header: dict, lines: dict, blocks: Any) -> bytes:
    """Landscape letter Application for Payment (U-792a): the figures on the left, the
    project's optional blocks stacked on the right; with every block off the figures
    take the full width.

    ``header``: project, application_no, application_date, period_to, from_lines
    (the first line is the contractor's name), to_name, to_lines. ``lines``: from ``build_application_lines``.
    ``blocks``: the project's resolved switches (``DrawPacketBlocks`` — attributes
    contractor_statement, owner_approval, architect_certificate, notary).
    """
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    BLACK = colors.black
    BAND = colors.HexColor(BAND_FILL)

    def esc(text: Any) -> str:
        return _html.escape(str(text)) if text not in (None, "") else ""

    band_style = ParagraphStyle("pa_band", fontName=SERIF_BOLD, fontSize=10, leading=12)
    band_right = ParagraphStyle("pa_band_r", parent=band_style, alignment=TA_RIGHT)
    label_style = ParagraphStyle("pa_label", fontName=SERIF, fontSize=9.5, leading=12)
    label_bold = ParagraphStyle("pa_label_b", fontName=SERIF_BOLD, fontSize=10, leading=12)
    dollar_style = ParagraphStyle("pa_dollar", fontName=SERIF, fontSize=9.5, leading=12)
    dollar_bold = ParagraphStyle("pa_dollar_b", fontName=SERIF_BOLD, fontSize=10, leading=12)
    money_style = ParagraphStyle("pa_money", fontName=SERIF, fontSize=9.5, leading=12,
                                 alignment=TA_RIGHT)
    money_bold = ParagraphStyle("pa_money_b", fontName=SERIF_BOLD, fontSize=10, leading=12,
                                alignment=TA_RIGHT)
    note_style = ParagraphStyle("pa_note", fontName=SERIF_ITALIC, fontSize=8.5, leading=11)
    block_head = ParagraphStyle("pa_block_h", fontName=SERIF_BOLD, fontSize=9.5, leading=12)
    block_body = ParagraphStyle("pa_block_b", fontName=SERIF, fontSize=9, leading=12.5)

    _, usable_w = landscape_doc(io.BytesIO(), "Application for Payment")
    from_lines = header.get("from_lines") or []
    contractor = from_lines[0] if from_lines else "The Contractor"
    owner = header.get("to_name") or ""

    head = document_header(
        title="Application for Payment",
        from_lines=from_lines,
        to_name=owner,
        to_lines=header.get("to_lines") or [],
        fields=application_header_fields(header),
        usable_w=usable_w,
    )

    # ---- figures -----------------------------------------------------------
    # None renders as an empty cell, not the house "-" placeholder: a dash in a money
    # column reads as $0, and a missing contract sum is not a $0 one (U-791).
    def row(label: str, value: Optional[Decimal], *, bold: bool = False):
        return [
            Paragraph(label, label_bold if bold else label_style),
            Paragraph("$", dollar_bold if bold else dollar_style),
            Paragraph("" if value is None else money_number(value),
                      money_bold if bold else money_style),
        ]

    retainage = _dec(lines.get("retainage"))
    data = [
        [Paragraph("DESCRIPTION", band_style), Paragraph("AMOUNT", band_right), ""],
        row("Contract Sum", _dec_or_none(lines.get("contract_sum"))),
        row("Net Change by Change Orders", _dec(lines.get("net_change_orders"))),
        row("Contract Sum to Date", _dec_or_none(lines.get("contract_sum_to_date"))),
        row("Work Completed to Date", _dec(lines.get("completed_to_date"))),
    ]
    if retainage:
        data.append(row("Less Retainage Held", retainage))
        data.append(row("Completed to Date, Less Retainage",
                        _dec(lines.get("earned_less_retainage"))))
    data.append(row("Less Previous Applications", _dec(lines.get("previous_applications"))))
    due_idx = len(data)
    data.append(row("Amount Due This Application", _dec(lines.get("amount_due")), bold=True))
    data.append(row("Balance to Finish", _dec_or_none(lines.get("balance_to_finish"))))

    # ---- optional blocks (per-project switches) -----------------------------
    blank = "_" * 24
    short = "_" * 12
    by_date = f"By: {blank}&nbsp;&nbsp;&nbsp;Date: {short}"

    def block(title: str, body_lines: list[str]) -> list:
        return [
            Paragraph(esc(title), block_head),
            Spacer(1, 2),
            Paragraph("<br/>".join(body_lines), block_body),
            Spacer(1, 0.14 * inch),
        ]

    block_flowables: list = []
    if blocks.contractor_statement:
        block_flowables += block("Contractor's Statement", [
            f"{esc(contractor)} states that the work and materials billed in this application "
            "were furnished for this project as described, that the amounts shown reflect "
            "costs incurred, and that the amount due this application is now payable.",
            "",
            esc(contractor),
            by_date,
        ])
    if blocks.notary:
        block_flowables += block("Notary", [
            f"State of: {short}&nbsp;&nbsp;&nbsp;County of: {short}",
            f"Subscribed and sworn to before me on {short},",
            f"by {blank}.",
            f"Notary Public: {blank}",
            f"My commission expires: {short}",
        ])
    if blocks.owner_approval:
        owner_label = esc(owner) if owner else "Owner"
        block_flowables += block("Owner Approval", [
            f"Approved for payment in the amount of $ {blank}",
            "",
            owner_label,
            by_date,
        ])
    if blocks.architect_certificate:
        block_flowables += block("Architect Review", [
            "The architect has reviewed this application and recommends payment in the "
            f"amount of $ {blank}",
            "",
            f"Architect: {blank}",
            by_date,
        ])

    gutter = 0.3 * inch
    figures_w = usable_w * 0.55 if block_flowables else usable_w
    dollar_w = 0.22 * inch
    amount_w = 1.45 * inch
    figures = Table(data, colWidths=[figures_w - dollar_w - amount_w, dollar_w, amount_w])
    figures.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("SPAN", (1, 0), (2, 0)),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
        ("LEFTPADDING", (2, 1), (2, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEABOVE", (1, due_idx), (2, due_idx), 0.75, BLACK),
        ("LINEBELOW", (1, due_idx), (2, due_idx), 0.75, BLACK),
    ]))
    figures_col = [
        figures,
        Spacer(1, 4),
        Paragraph("The attached Schedule of Values details these amounts by cost code.",
                  note_style),
    ]

    def two_column() -> list:
        body = Table([[figures_col, block_flowables]],
                     colWidths=[figures_w + gutter, usable_w - figures_w - gutter])
        body.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), gutter),
            ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return [body]

    def render(body: list) -> bytes:
        buf = io.BytesIO()
        doc, _ = landscape_doc(buf, "Application for Payment")
        doc.build([head, Spacer(1, 0.18 * inch), *body])
        return buf.getvalue()

    if not block_flowables:
        return render(figures_col)
    # The two-column row cannot split, so when an unusually long name or address
    # makes it taller than the page (LayoutError) or pushes it whole onto page 2,
    # fall back to stacking the blocks under the figures, which split normally
    # (U-792a review F1/F2).
    from pypdf import PdfReader
    from reportlab.platypus.doctemplate import LayoutError

    try:
        pdf = render(two_column())
        if len(PdfReader(io.BytesIO(pdf)).pages) == 1:
            return pdf
    except LayoutError:
        pass
    return render([*figures_col, Spacer(1, 0.18 * inch), *block_flowables])
