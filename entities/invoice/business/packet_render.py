"""Shared building blocks for the AIA draw-packet pages (G702, G703, Draw Request,
Trend) so every page carries the same serif type, Rogers Build mark, and cost-code
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


def format_cc_number(raw: Any, decimals: int) -> str:
    """Render a cost-code number the way the manual packets do: a fixed number of
    decimal places (G703 uses 4 → ``2.0000``; the Invoice/Trend use 3 → ``2.000``).
    Leading-zero stored forms collapse to their numeric value (``"02" → "2.0000"``);
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
