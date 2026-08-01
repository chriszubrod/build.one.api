"""Pure cover-page rollup + PDF for invoice draw packets (U-191)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


@dataclass(frozen=True)
class CoverCategory:
    cost_code_number: str
    cost_code_name: str
    amount: Decimal


@dataclass(frozen=True)
class CoverRollup:
    categories: list[CoverCategory]
    subtotal: Decimal
    builders_fee: Decimal
    total: Decimal
    fee_rate: Optional[Decimal] = None


def _is_source_linked(row: dict) -> bool:
    # Mirror the packet's `toc_items` filter EXACTLY (router
    # `_generate_invoice_packet`: `source_type != "Manual"`) so the cover rolls up
    # the identical row set the expanded TOC totals — the reconciliation invariant
    # (cover.subtotal == expanded-TOC grand total) then holds by construction, and
    # a None-source anomaly line lands in both surfaces or neither, never split.
    return row.get("source_type") != "Manual"


def _signed_line_amount(row: dict) -> Optional[Decimal]:
    """
    Client-facing line amount in Decimal — the single source of truth for the
    packet's line sign rules (the router's float `_toc_signed_amount` delegates
    here, so the cover and TOC can never disagree on a line's sign or magnitude).

    Prefer billed_price (the SOURCE line's marked-up, client-billed price =
    Amount + Markup — the ILI itself carries only the un-marked-up base because
    the QBO invoice splits markup into separate Manual lines); fall back to the
    ILI's own price then amount for sources with no marked-up Price column
    (BillCreditLineItem, EmployeeLaborLineItem). Credits are stored as positive
    magnitudes, so negate when positive to reflect the customer-facing reduction:
      - BillCreditLineItem source (VendorCredit / credit memo)
      - ExpenseLineItem whose parent Expense.IsCredit = True (expense refund).
    """
    p = row.get("billed_price")
    if p is None:
        p = row.get("price")
    if p is None:
        p = row.get("amount")
    if p is None:
        return None
    try:
        v = Decimal(str(p))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if v > 0:
        st = row.get("source_type")
        if st == "BillCreditLineItem":
            v = -v
        elif st == "ExpenseLineItem" and row.get("is_credit"):
            v = -v
    return v


def _cost_code_sort_key(number: str) -> tuple:
    cc = (number or "").strip()
    try:
        return (float(cc), cc.lower())
    except (ValueError, TypeError):
        return (float("inf"), cc.lower())


def build_cover_rollup(enriched_lines: list[dict], fee_rate: Any) -> CoverRollup:
    """
    Roll up source-linked enriched lines by parent cost code (number + name).

    Amounts use billed_price → price → amount with credit negation; all Decimal.
    """
    # Group by cost-code NUMBER only — the exact key the expanded TOC groups on
    # (router `_build_toc_expanded_pdf`) — so each cover category reconciles to the
    # TOC's per-group subtotal, not just the grand total. Number↔name are 1:1 via
    # the CostCode join, so carrying a first-non-empty name for display can never
    # split a number across rows.
    groups: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    for row in enriched_lines:
        if not _is_source_linked(row):
            continue
        amt = _signed_line_amount(row)
        if amt is None:
            continue
        number = row.get("cost_code_number") or ""
        name = row.get("cost_code_name") or ""
        if number not in groups:
            groups[number] = Decimal("0")
            names[number] = name
        groups[number] += amt
        if not names[number] and name:
            names[number] = name

    categories = [
        CoverCategory(cost_code_number=number, cost_code_name=names[number], amount=v)
        for number, v in groups.items()
    ]
    categories.sort(key=lambda c: _cost_code_sort_key(c.cost_code_number))

    subtotal = sum((c.amount for c in categories), Decimal("0"))
    fee_dec: Optional[Decimal] = None
    if fee_rate is not None:
        fee_dec = Decimal(str(fee_rate))
        builders_fee = (subtotal * fee_dec).quantize(Decimal("0.01"))
    else:
        builders_fee = Decimal("0")
    total = subtotal + builders_fee

    return CoverRollup(
        categories=categories,
        subtotal=subtotal,
        builders_fee=builders_fee,
        total=total,
        fee_rate=fee_dec,
    )


def _format_money(value: Optional[Decimal]) -> str:
    if value is None:
        return "—"
    if value < 0:
        return f"(${abs(value):,.2f})"
    return f"${value:,.2f}"


def build_cover_pdf(header: dict, cover_model: CoverRollup) -> bytes:
    """ReportLab cover page: category table + subtotal / fee / total footer rows."""
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    BLUE = colors.HexColor("#1F3864")

    wrap_style = ParagraphStyle("cover_wrap", fontName="Helvetica", fontSize=8, leading=10)
    wrap_hdr = ParagraphStyle(
        "cover_wrap_hdr", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=BLUE
    )
    bold_right = ParagraphStyle(
        "cover_bold_right", fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_RIGHT
    )
    hdr_right = ParagraphStyle(
        "cover_hdr_right",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=BLUE,
        alignment=TA_RIGHT,
    )

    def W(text):
        return Paragraph(_html.escape(str(text)) if text else "", wrap_style)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    col_widths = [55, 280, 169]
    headers = [
        Paragraph("Cost Code", wrap_hdr),
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
    if cover_model.fee_rate is not None:
        table_data.append([
            "",
            Paragraph("Builder's Fee", bold_right),
            Paragraph(_format_money(cover_model.builders_fee), bold_right),
        ])
    table_data.append([
        "",
        Paragraph("Total", bold_right),
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

    title = header.get("title") or "Invoice Summary"
    story = [
        Paragraph(
            title,
            ParagraphStyle(
                "CoverTitle",
                fontName="Helvetica-Bold",
                fontSize=12,
                textColor=BLUE,
                alignment=TA_CENTER,
                spaceAfter=6,
            ),
        ),
    ]
    for label, key in (
        ("Project", "project_name"),
        ("Invoice", "invoice_number"),
        ("Date", "invoice_date"),
    ):
        val = header.get(key)
        if val:
            story.append(
                Paragraph(
                    f"{label}: {_html.escape(str(val))}",
                    ParagraphStyle(
                        "CoverMeta",
                        fontName="Helvetica",
                        fontSize=9,
                        textColor=BLUE,
                        alignment=TA_CENTER,
                        spaceAfter=2,
                    ),
                )
            )
    story.append(table)

    doc.build(story)
    return buf.getvalue()
