"""Pure cost-code rollup for invoice draw packets (U-191).

`build_cover_rollup` is the shared data source + reconciliation check for the draw
packet: the router's float `_toc_signed_amount` delegates to `_signed_line_amount`,
and the Draw Request renderer (draw_request.py, U-204) consumes the rollup + reuses
`_format_money`. The old `build_cover_pdf` cover page was retired in U-204 — the
Draw Request page replaces it in the packet.
"""

from __future__ import annotations

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
