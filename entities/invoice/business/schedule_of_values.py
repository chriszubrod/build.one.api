"""Schedule of Values — assembler (Budget schedule x draws) + landscape renderer.

The draw packet's per-cost-code progress page: what each line is scheduled at, what
earlier applications billed, what this application bills, the total to date, % complete
and the balance to finish. It is our own document (U-792), styled like the Draw Request
(serif type, the Rogers Build mark, a gray column-header band, 3-decimal cost-code
numbers) — not a reproduction of any published industry form.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any, Optional

from entities.invoice.business.cover import _cost_code_sort_key, _format_money
from entities.invoice.business.packet_render import (
    BAND_FILL,
    SERIF,
    SERIF_BOLD,
    application_header_fields,
    dec as _dec,
    document_header,
    format_cc_number,
    landscape_doc,
)

# Schedule-of-values line number for the Builder's Fee. It is driven by each draw's
# COMPUTED fee (draw-financials), not by a work cost-code category, so it's split out
# of the work rows and rebuilt as a synthetic line.
FEE_ITEM_NUMBER = "90"


def _money(value: Any) -> str:
    return _format_money(_dec(value))


def _money_or_blank(value: Any) -> str:
    """Blank for None — the no-schedule columns (scheduled value, balance) render empty
    (U-791). Not the house "-" placeholder: in a money column a dash reads as $0."""
    return "" if value is None else _money(value)


def _fmt_pct(total_to_date: Decimal, scheduled: Decimal) -> str:
    """% complete (to date / scheduled). $0 scheduled → '0.00%'."""
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


def normalize_draw_date(value: Any) -> str:
    """The 'YYYY-MM-DD' truncation draw_financials.py stamps on every draw's ``date``.
    Callers computing ``current_date`` for build_sov_rows must go through this (not
    re-derive the truncation inline) so the two never silently drift apart."""
    return str(value or "")[:10]


def build_sov_rows(sov: list, draws: list, current_label: str,
                    fee_item_number: str = FEE_ITEM_NUMBER,
                    current_date: Any = None):
    """Assemble the Schedule of Values (rows, grand) from the live Budget schedule +
    the draw-financials.

    ``sov``: [{cost_code_number, cost_code_name, scheduled_value}] — the budget
    schedule (``scheduled``). ``draws``: the project's coded draws (ordered), each
    {label, date, categories, subtotal, builders_fee, total}. ``current_label``: the
    invoice being packeted — splits prior draws (``prev``, previous applications) from
    this one (``this_period``). ``current_date``: that invoice's date — normalized
    internally via ``normalize_draw_date`` (so callers need not pre-normalize) — used
    ONLY to disambiguate when two draws share ``current_label`` (labels are the
    invoice_number, unique per project by DB constraint under normal writes; this is a
    defensive edge for legacy data / direct SQL, per the U-207 adversarial-review
    finding). With a single label match (the common case) behavior is unchanged. With
    no date match, or with BOTH label and date tied (an even deeper duplicate — two
    rows identical on both fields, which ``draws`` carries no further identity to
    break), falls back to the first match — never raises. That double-tie residual
    can't be resolved without a stable id on each draw, which the draw-financials
    shape deliberately doesn't carry today (see the caller in
    ``entities/invoice/api/router.py``). Row set = SoV cost codes UNION any draw cost
    code not in the budget (over-budget → scheduled $0, negative balance). The
    Builder's Fee line (``fee_item_number``) is a synthetic row: scheduled from the
    budget fee line, prev = sum of prior draws' computed fees, this_period = the
    current draw's fee.

    No schedule of values (U-791): an empty ``sov`` means the project has no budget.
    The rows then come from the draws alone, and ``scheduled``, ``pct`` and
    ``balance`` are None on every row and on the grand total — they render blank
    rather than as $0 / a negative balance. prev / this_period / total_to_date are
    unaffected.
    """
    has_schedule = bool(sov)
    current_date = normalize_draw_date(current_date) if current_date else None
    label_matches = [i for i, d in enumerate(draws) if d.get("label") == current_label]
    if not label_matches:
        cur_idx = len(draws)
    else:
        date_matches = ([i for i in label_matches if draws[i].get("date") == current_date]
                        if current_date else [])
        cur_idx = date_matches[0] if len(date_matches) == 1 else label_matches[0]
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

    # Name collection doesn't need the prior/current split (only the amounts do).
    for d in prior + ([current] if current else []):
        _absorb_names(d)

    prev: dict = {}
    for d in prior:
        for num, amt in _work_amounts(d).items():
            prev[num] = prev.get(num, Decimal("0")) + amt
    cur: dict = _work_amounts(current) if current else {}

    # Identify the budget's Builder's Fee schedule-of-values line — the conventional
    # item number (exact, OR the integer part so 90 / 90.0 / 90.01 all resolve) OR the
    # "Builder's Fee" name. It drives fee_scheduled and is EXCLUDED from the work rows: a
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

    def _make_row(num, scheduled_value, prev_amt, this_amt, name):
        to_date = prev_amt + this_amt
        return {
            "item_no": num, "description": name or "",
            "scheduled": scheduled_value if has_schedule else None,
            "prev": prev_amt, "this_period": this_amt,
            "total_to_date": to_date,
            "pct": _fmt_pct(to_date, scheduled_value) if has_schedule else None,
            "balance": scheduled_value - to_date if has_schedule else None,
        }

    rows = [
        _make_row(num, scheduled.get(num, Decimal("0")), prev.get(num, Decimal("0")),
                  cur.get(num, Decimal("0")), names.get(num, ""))
        for num in sorted(work_codes, key=_cost_code_sort_key)
    ]

    # Synthetic Builder's Fee line: scheduled from the budget fee line (if any), prev =
    # sum of prior draws' computed fees, this_period = the current draw's fee.
    fee_scheduled = scheduled.get(fee_number, Decimal("0")) if fee_number else Decimal("0")
    fee_prev = sum((_dec(d.get("builders_fee")) for d in prior), Decimal("0"))
    fee_this = _dec(current.get("builders_fee")) if current else Decimal("0")
    rows.append(_make_row(fee_number or fee_item_number, fee_scheduled, fee_prev, fee_this,
                          names.get(fee_number, "Builder's Fee")))

    grand_prev = sum((r["prev"] for r in rows), Decimal("0"))
    grand_this = sum((r["this_period"] for r in rows), Decimal("0"))
    grand_total = grand_prev + grand_this
    grand = {
        "scheduled": None, "prev": grand_prev, "this_period": grand_this,
        "total_to_date": grand_total, "pct": None, "balance": None,
    }
    if has_schedule:
        grand_scheduled = sum((r["scheduled"] for r in rows), Decimal("0"))
        grand.update(scheduled=grand_scheduled,
                     pct=_fmt_pct(grand_total, grand_scheduled),
                     balance=grand_scheduled - grand_total)
    return rows, grand


def build_schedule_of_values_pdf(header: dict, rows: list[dict], grand: dict) -> bytes:
    """Landscape letter Schedule of Values in the Draw Request style.

    ``header``: project, application_no, application_date, period_to, from_lines,
    to_name, to_lines. ``rows`` / ``grand``: from ``build_sov_rows``.
    """
    import html as _html

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import LongTable, Paragraph, Spacer, TableStyle

    BLACK = colors.black
    BAND = colors.HexColor(BAND_FILL)
    FONT_SIZE = 8
    LEADING = FONT_SIZE + 2

    def P(text: str, style: ParagraphStyle) -> Paragraph:
        return Paragraph(_html.escape(text) if text else "", style)

    band_style = ParagraphStyle("sov_band", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=LEADING, alignment=TA_CENTER)
    band_left = ParagraphStyle("sov_band_l", parent=band_style, alignment=TA_LEFT)
    cell_style = ParagraphStyle("sov_cell", fontName=SERIF, fontSize=FONT_SIZE,
                                leading=LEADING, alignment=TA_LEFT)
    item_style = ParagraphStyle("sov_item", parent=cell_style, alignment=TA_RIGHT)
    money_style = ParagraphStyle("sov_money", parent=cell_style, alignment=TA_RIGHT)
    bold_right = ParagraphStyle("sov_bold_r", fontName=SERIF_BOLD, fontSize=FONT_SIZE,
                                leading=LEADING, alignment=TA_RIGHT)
    bold_left = ParagraphStyle("sov_bold_l", parent=bold_right, alignment=TA_LEFT)

    buf = io.BytesIO()
    doc, usable_w = landscape_doc(buf, "Schedule of Values")

    item_w = 0.6 * inch
    desc_w = 2.4 * inch
    pct_w = 0.85 * inch
    money_w = (usable_w - item_w - desc_w - pct_w) / 5.0
    col_widths = [item_w, desc_w, money_w, money_w, money_w, money_w, pct_w, money_w]

    head = document_header(
        title="Schedule of Values",
        from_lines=header.get("from_lines") or [],
        to_name=header.get("to_name") or "",
        to_lines=header.get("to_lines") or [],
        fields=application_header_fields(header),
        usable_w=usable_w,
    )

    band = [
        P("ITEM", band_style),
        P("DESCRIPTION", band_left),
        P("SCHEDULED VALUE", band_style),
        P("PREVIOUS APPLICATIONS", band_style),
        P("THIS APPLICATION", band_style),
        P("COMPLETED TO DATE", band_style),
        P("% COMPLETE", band_style),
        P("BALANCE TO FINISH", band_style),
    ]
    table_data: list[list] = [band]
    for row in rows:
        table_data.append([
            P(format_cc_number(row.get("item_no"), 3), item_style),
            P(row.get("description") or "", cell_style),
            P(_money_or_blank(row["scheduled"]), money_style),
            P(_money(row["prev"]), money_style),
            P(_money(row["this_period"]), money_style),
            P(_money(row["total_to_date"]), money_style),
            P(row.get("pct") or "", money_style),
            P(_money_or_blank(row["balance"]), money_style),
        ])

    total_idx = len(table_data)
    table_data.append([
        "",
        P("Totals", bold_left),
        P(_money_or_blank(grand["scheduled"]), bold_right),
        P(_money(grand["prev"]), bold_right),
        P(_money(grand["this_period"]), bold_right),
        P(_money(grand["total_to_date"]), bold_right),
        P(grand.get("pct") or "", bold_right),
        P(_money_or_blank(grand["balance"]), bold_right),
    ])

    table = LongTable(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("VALIGN", (0, 1), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 1), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 1), (-1, total_idx - 1), 0.25, colors.HexColor("#D9D9D9")),
        ("LINEABOVE", (2, total_idx), (-1, total_idx), 0.75, BLACK),
        ("TOPPADDING", (0, total_idx), (-1, total_idx), 4),
    ]))

    doc.build([head, Spacer(1, 0.18 * inch), table])
    return buf.getvalue()
