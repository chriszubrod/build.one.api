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
block, an owner approval line and an architect review. The two-column shape holds
even with every block off (U-792b).
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

# The optional blocks' type size and the height of one handwriting row (a fill-in or
# signature line). 0.32in leaves room to write and still fits all four blocks on one
# landscape page beside the figures.
BLOCK_FONT = 9
WRITE_ROW_H = 0.32 * 72  # 0.32in, in points
FIELD_GAP = 6  # points between two fields on one writing row
NAME_LINE_GAP = 3  # points between a block's text and its signer-name line
BLOCK_GAP = 4  # points after each block


def build_application_lines(grand: dict, retainage_rate: Any = Decimal("0"),
                            original_contract_sum: Any = None) -> dict:
    """The Application for Payment figures, derived from the Schedule of Values GRAND
    totals so the two pages reconcile by construction.

    ``grand`` is the second return of ``schedule_of_values.build_sov_rows`` (keys:
    original, co_prev, co_this, scheduled, prev, this_period, total_to_date).
    ``contract_sum`` is the original schedule total and ``net_change_orders`` the
    approved change orders through this application (previous + this period, U-793),
    so ``contract_sum_to_date`` equals the schedule's revised total. Override the
    original via ``original_contract_sum``. Retainage defaults to 0. Identities:
    contract_sum_to_date = contract_sum + net_change_orders; earned_less_retainage =
    completed_to_date - retainage; amount_due = earned_less_retainage -
    previous_applications (= grand this_period when retainage is 0);
    balance_to_finish = contract_sum_to_date - earned_less_retainage.

    No contract sum (U-791): when the grand has no schedule (``scheduled`` is None)
    and no ``original_contract_sum`` is given, contract_sum, contract_sum_to_date and
    balance_to_finish are None and render blank — a $0 contract would print a negative
    balance to finish. The payment figures are unaffected.
    """
    has_schedule = grand.get("scheduled") is not None
    change_orders = (_dec(grand.get("co_prev")) + _dec(grand.get("co_this"))
                     if has_schedule else Decimal("0"))
    if original_contract_sum is not None:
        contract_sum = _dec(original_contract_sum)
    elif has_schedule:
        # A grand without the U-793 split (original absent) is all original.
        contract_sum = _dec(grand["original"] if grand.get("original") is not None else grand["scheduled"])
    else:
        contract_sum = None
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
    """Landscape letter Application for Payment (U-792a/b): a full-width header, then two
    columns — the figures on the left, the project's optional blocks stacked on the
    right (left blank when every block is off). Fill-ins and signatures are drawn
    rules on handwriting-height rows.

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
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

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
    block_body = ParagraphStyle("pa_block_b", fontName=SERIF, fontSize=BLOCK_FONT, leading=12.5)

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

    # ---- layout: full-width header, then two columns ------------------------
    # Figures always take the left column and the blocks the right, even when every
    # block is off (Chris, U-792b) — the page keeps one shape across projects.
    gutter = 0.3 * inch
    # 42/58: the signature column gets the extra width — signatures and State/County
    # need room to write (Chris, U-792c); the figures need far less than half the page.
    # Was 45/55 at 0.5in sides; at the packet's 0.75in sides (U-793e) 42% keeps the
    # blocks column at its old ~5.2in so the signature rules keep their length.
    figures_w = usable_w * 0.42
    blocks_w = usable_w - figures_w - gutter

    # ---- optional blocks (per-project switches) -----------------------------
    # Fill-ins and signatures are drawn rules sized to the column, each on a row
    # tall enough to write on (U-792b) — not typed underscores.
    def field_row(fields: list) -> Table:
        """One writing row: ``[(label, weight), …]`` → label, then a rule taking a
        ``weight`` share of the width left after the labels."""
        label_ws = [stringWidth(label, SERIF, BLOCK_FONT) + 4 for label, _ in fields]
        free = blocks_w - sum(label_ws) - FIELD_GAP * (len(fields) - 1)
        total = sum(weight for _, weight in fields)
        cells, widths, style = [], [], [
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]
        for k, ((label, weight), label_w) in enumerate(zip(fields, label_ws)):
            if k:
                cells.append("")
                widths.append(FIELD_GAP)
            cells += [Paragraph(esc(label), block_body), ""]
            widths += [label_w, free * weight / total]
            col = len(cells) - 1
            style.append(("LINEBELOW", (col, 0), (col, 0), 0.6, BLACK))
        t = Table([cells], colWidths=widths, rowHeights=[WRITE_ROW_H])
        t.hAlign = "LEFT"  # stays under its heading in the stacked fallback too
        t.setStyle(TableStyle(style))
        return t

    by_date = [("By:", 2.5), ("Date:", 1)]

    block_groups: list = []

    def block(title: str, parts: list) -> list:
        group = [Paragraph(esc(title), block_head), *parts, Spacer(1, BLOCK_GAP)]
        block_groups.append(group)
        return group

    block_flowables: list = []
    if blocks.contractor_statement:
        block_flowables += block("Contractor's Statement", [
            Paragraph(
                f"{esc(contractor)} states that the work and materials billed in this application "
                "were furnished for this project as described, that the amounts shown reflect "
                "costs incurred, and that the amount due this application is now payable.",
                block_body),
            Spacer(1, NAME_LINE_GAP),
            Paragraph(esc(contractor), block_body),
            field_row(by_date),
        ])
    if blocks.notary:
        block_flowables += block("Notary", [
            field_row([("State of:", 1), ("County of:", 1)]),
            field_row([("Subscribed and sworn to before me on:", 1)]),
            field_row([("by:", 1)]),
            field_row([("Notary Public:", 1)]),
            field_row([("Commission expires:", 1)]),
        ])
    if blocks.owner_approval:
        block_flowables += block("Owner Approval", [
            field_row([("Approved amount: $", 1)]),
            Spacer(1, NAME_LINE_GAP),
            Paragraph(esc(owner) if owner else "Owner", block_body),
            field_row(by_date),
        ])
    if blocks.architect_certificate:
        block_flowables += block("Architect Review", [
            Paragraph("The architect has reviewed this application and recommends payment "
                      "of the amount below.", block_body),
            field_row([("Recommended amount: $", 1)]),
            field_row([("Architect:", 1)]),
            field_row(by_date),
        ])

    dollar_w = 0.22 * inch
    amount_w = 1.45 * inch
    figures = Table(data, colWidths=[figures_w - dollar_w - amount_w, dollar_w, amount_w])
    figures.hAlign = "LEFT"
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
        body = Table([[figures_col, block_flowables or ""]],
                     colWidths=[figures_w + gutter, blocks_w])
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
        doc.build([head, Spacer(1, 0.1 * inch), *body])
        return buf.getvalue()

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
    # Each block kept whole so a page break never strands a title or splits a
    # signature from its name line (U-792b review F2).
    return render([*figures_col, Spacer(1, 0.18 * inch),
                   *(KeepTogether(group) for group in block_groups)])
