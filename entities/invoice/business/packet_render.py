"""Shared building blocks for the draw-packet pages (Application for Payment, Schedule
of Values, Draw Request, Trend) so every page carries the same serif type, Rogers Build mark, and cost-code
number formatting as the manually produced packets they replace.

Deliberately tiny + dependency-light: the four page renderers each build their own
ReportLab story, but pull the cross-page constants/helpers from here so the "house
style" lives in one place.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# The manual packets are set in a serif face (Times/Cambria). ReportLab ships the
# Times family built in, so no font embedding is required to match the look.
SERIF = "Times-Roman"
SERIF_BOLD = "Times-Bold"
SERIF_ITALIC = "Times-Italic"
SERIF_BOLD_ITALIC = "Times-BoldItalic"

# The Invoice/Trend column-header band is a flat medium gray with dark bold text.
BAND_FILL = "#BFBFBF"

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "rogers_build_logo.png")
_LOGO_NATIVE_W = 232.0  # px — extracted from the reference packet
_LOGO_NATIVE_H = 215.0


def as_decimal(value: Any) -> Decimal:
    """Coerce a draw-financials value to Decimal, mapping None/blank/0 to Decimal(0).
    Shared by the packet data layer (draw_financials) and renderers (trend) so the
    None convention can't drift between them."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def dec(value: Any) -> Decimal:
    """Coerce a money value to Decimal; None → Decimal(0). Unlike ``as_decimal`` it is
    None-only: a blank string raises rather than silently becoming 0."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def format_cc_number(raw: Any, decimals: int) -> str:
    """Render a cost-code number the way the manual packets do: a fixed number of
    decimal places (every packet page uses 3 → ``2.000``). Leading-zero stored forms
    collapse to their numeric value (``"02" → "2.000"``);
    a genuinely non-numeric code is returned unchanged rather than dropped.
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    try:
        return f"{Decimal(s):.{decimals}f}"
    except (InvalidOperation, ValueError):
        return s


def logo_flowable(max_width: float, max_height: float):
    """A ReportLab ``Image`` of the Rogers Build mark, scaled to fit within
    ``max_width`` × ``max_height`` (points) while preserving aspect ratio. Returns
    ``None`` when the asset is missing so a page still renders logo-less rather than
    raising (the packet is failure-isolated at the router)."""
    if not os.path.exists(_LOGO_PATH):
        return None
    from reportlab.platypus import Image

    ratio = _LOGO_NATIVE_W / _LOGO_NATIVE_H
    width = max_width
    height = width / ratio
    if height > max_height:
        height = max_height
        width = height * ratio
    img = Image(_LOGO_PATH, width=width, height=height)
    img.hAlign = "RIGHT"
    return img


def money_number(value: Optional[Decimal]) -> str:
    """The numeric half of a split ``$ | 1,234.56`` money cell (no leading ``$``).
    ``None`` → an en-dash placeholder, matching the manual packet's empty cells."""
    if value is None:
        return "-"
    if value < 0:
        return f"({abs(value):,.2f})"
    return f"{value:,.2f}"


def application_header_fields(header: dict) -> list:
    """The field strip the Application for Payment and Schedule of Values share, so the
    two pages' headers agree by construction."""
    return [
        ("Project", header.get("project") or ""),
        ("Application No.", header.get("application_no") or ""),
        ("Application Date", header.get("application_date") or ""),
        ("Period To", header.get("period_to") or ""),
    ]


def document_header(*, title: str, from_lines: list, to_name: str, to_lines: list,
                    fields: list, usable_w: float, left_share: float = 0.78,
                    from_share: float = 0.48, logo_max_h_in: float = 0.95,
                    field_shares: Optional[list] = None):
    """The header the Application for Payment and Schedule of Values share (U-792):
    the page title over a From / To block with the Rogers Build mark top-right, then
    one ruled strip of ``(label, value)`` fields (project, application no., …).

    The layout knobs default to these pages' values; they are parameters so the Draw
    Request / Trend headers (which differ only in these ratios) can move onto this
    helper later (TODO: "finish the extraction")."""
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Table, TableStyle

    def esc(text: Any) -> str:
        return _html.escape(str(text)) if text not in (None, "") else ""

    title_style = ParagraphStyle("hdr_title", fontName=SERIF_BOLD, fontSize=15, leading=18)
    party_style = ParagraphStyle("hdr_party", fontName=SERIF, fontSize=9.5, leading=12)
    label_style = ParagraphStyle("hdr_label", fontName=SERIF_BOLD, fontSize=7.5, leading=9)
    value_style = ParagraphStyle("hdr_value", fontName=SERIF, fontSize=9.5, leading=12)

    from_body = "<br/>".join(esc(ln) for ln in from_lines if ln)
    to_parts = [f"To:&nbsp;&nbsp;{esc(to_name)}"] if to_name else ["To:"]
    to_parts.extend(esc(ln) for ln in to_lines if ln)

    left_w = usable_w * left_share
    from_to = Table(
        [[Paragraph(from_body, party_style), Paragraph("<br/>".join(to_parts), party_style)]],
        colWidths=[left_w * from_share, left_w * (1 - from_share)],
    )
    from_to.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    left_stack = Table([[Paragraph(esc(title), title_style)], [from_to]], colWidths=[left_w])
    left_stack.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 10),
    ]))
    logo = logo_flowable(usable_w - left_w, logo_max_h_in * inch)

    field_cells = [
        Paragraph(f"{esc(label).upper()}<br/><font name='{SERIF}' size='9.5'>{esc(value)}</font>",
                  label_style)
        for label, value in fields
    ]
    # By default the first field (the project name) is the long one: twice the share.
    shares = field_shares or ([2] + [1] * (len(field_cells) - 1) if field_cells else [])
    unit = usable_w / max(sum(shares), 1)
    strip = Table([field_cells], colWidths=[unit * share for share in shares])
    strip.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.75, colors.black),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    outer = Table([[left_stack, logo or ""], [strip, ""]], colWidths=[left_w, usable_w - left_w])
    outer.setStyle(TableStyle([
        ("SPAN", (0, 1), (1, 1)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
    ]))
    return outer
