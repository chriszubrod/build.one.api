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


def is_packet_row(row: dict) -> bool:
    """Does this line belong in the packet's TOCs, page merge and cover rollup?

    **The ONE definition.** The router's `_generate_invoice_packet` imports it
    for `toc_items`, `build_cover_rollup` applies it below, and
    `DrawFinancialsService._coded_rollup` reaches it through that rollup — so
    the Draw Request, the AIA/Trend pages and the merged pages cannot disagree
    about membership. Each of those three used to carry its own hand-copy.

    - A **source-linked** line is always in: it has a parent document the
      customer can check the charge against.
    - A **Manual** line is in only when it carries its own document
      (`dbo.InvoiceLineItemAttachment`, surfaced by `enrich_line_items` — so
      this is only meaningful on an *enriched* row). Manual lines are otherwise
      internal adjustments — offsets, reversals, and the markup siblings QBO
      splits out — which the customer has nothing to verify against. Markup
      lines additionally must stay out because their value is already inside the
      sibling source's marked-up `billed_price`.

    A documented Manual line left out would also unbalance the packet: its
    amount stays in the invoice total while dropping out of the rollup, so the
    Draw Request foots to a different number than the invoice. Incident detail:
    `tests/test_documented_manual_line_in_packet.py`.
    """
    if row.get("source_type") != "Manual":
        return True
    return bool((row.get("attachment_public_id") or "").strip())


def _signed_line_amount(row: dict) -> Optional[Decimal]:
    """
    Client-facing line amount in Decimal — the single source of truth for the
    packet's line sign rules (the router's float `_toc_signed_amount` delegates
    here, so the cover and TOC can never disagree on a line's sign or magnitude).

    **A Manual row SKIPS the `price` tier.** `price` is the source line's
    marked-up client price for a sourced row, but on a QBO-pulled Manual row the
    pull connector stores the QBO line's **unit** price there whenever the line
    carries a qty, while `amount` holds the extended `Line.Amount`. Reading
    `price` would print a per-unit figure as the line total, and — because QBO
    states a credit as qty −1 × a POSITIVE unit price — would silently drop the
    minus sign on a refund. The corpus holds 501 Manual lines where the two
    disagree, the worst a −$240,613.79 credit carrying price +$240,613.79. This
    tier was unreachable for Manual rows until they became packet-eligible.

    Prefer billed_price (the SOURCE line's marked-up, client-billed price =
    Amount + Markup — the ILI itself carries only the un-marked-up base because
    the QBO invoice splits markup into separate Manual lines); fall back to the
    ILI's own price then amount for sources with no marked-up Price column
    (BillCreditLineItem, EmployeeLaborLineItem). The `if v > 0` guard below is
    deliberately idempotent, NOT an unconditional negate — do NOT "simplify"
    it to `v = -v`, even once every row looks positive. BillCreditLineItem's
    Price/Amount is TRANSITIONING to signed-negative at the write site
    (U-344): new/relabeled rows already arrive negative and must pass through
    unchanged; only pre-fix positive rows still need negating here. An
    unconditional negate would double-negate the new rows the moment they
    land — see budget_variance.sql's matching landmine note for the SQL-side
    version of this same trap. ExpenseLineItem's Expense.IsCredit branch is a
    separate mechanism U-344 does not touch.
      - BillCreditLineItem source (VendorCredit / credit memo)
      - ExpenseLineItem whose parent Expense.IsCredit = True (expense refund).
    """
    p = row.get("billed_price")
    if p is None and row.get("source_type") != "Manual":
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
        if not is_packet_row(row):
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
