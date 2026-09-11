# Python Standard Library Imports
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional


def to_decimal_or_none(value: Any) -> Optional[Decimal]:
    """Coerce API money fields to ``Decimal`` without dropping zero.

    This is the single money-coercion seam for entity API routers (U-196) and
    for the business-layer completion/finalize paths (U-199). It is
    None-preserving and must never be replaced by a truthy guard (``if value``):
    ``Decimal(0)`` is falsy in Python, so a truthy guard drops a genuine $0.00
    or 0% markup to ``None``. Downstream services preserve-on-``None``, so the
    write is silently discarded and the stale stored value is retained (U-194,
    U-196).

    Pydantic 2.11 already validates these schema fields to a true ``Decimal``
    for float, int, and string JSON input, so in practice the ``isinstance``
    branch is the only one taken and the coercion is a no-op.

    The ``Decimal(str(value))`` fallback is retained deliberately, not as dead
    code: it is the CLAUDE.md exact-decimal path for the day a schema field is
    loosened to float/str/int or an internal caller passes a raw value. Bare
    ``return value`` would let a float reach SQL silently.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def round_money(amount: Decimal) -> Decimal:
    """Quantize a money amount to two decimal places (cent precision).

    Uses ``ROUND_HALF_UP``, which is **half away from zero** in Python's
    ``decimal`` module (ties go away from zero — see the stdlib docs for
    ``ROUND_HALF_UP``). That matches T-SQL ``ROUND(x, 2)`` and the web client's
    ``roundMoney`` in ``build.one.web/src/shared/money.ts``.

    Do **not** rely on the ``decimal`` module default ``ROUND_HALF_EVEN``
    (banker's rounding): for labor and construction money here, half-even is
    wrong and can disagree with SQL and the web on tie cases.
    """
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def labor_price_two_shot(
    hours: Any,
    rate: Any,
    markup: Any,
) -> Optional[Decimal]:
    """Canonical Python labor price: two-shot cent rounding (U-203).

    Policy (same as time-entry aggregation and the web money helpers):

    1. ``cost = round_money(hours × rate)``
    2. ``price = round_money(cost × (1 + markup))``

    Markup ``None`` is treated as ``Decimal(0)`` (no markup). Returns ``None``
    if ``hours`` or ``rate`` is ``None``. Inputs are coerced via
    ``to_decimal_or_none`` (never ``float()``).

    Peer implementations: ``dbo.AggregateTimeEntryOnSubmit`` in
    ``entities/time_entry/sql/dbo.time_entry.sql`` and
    ``build.one.web/src/shared/money.ts`` (``computeAmount`` then
    ``applyMarkup``).

    Related, and deliberately NOT this function (U-424):
    ``dbo.UpdateContractLaborAggregates`` in
    ``entities/contract_labor/sql/dbo.contract_labor.sql`` derives a
    ContractLabor PARENT total by summing its children's already-rounded
    ``Price`` values, so a parent can land a cent above what this two-shot
    would produce from the parent's own hours × rate. That is intended: the
    vendor is billed the sum of the line prices. Do not "fix" a parent total
    by routing it through here.

    A **single-shot** ``round_money(hours × rate × (1 + markup))`` can land a
    cent off — e.g. ``0.75`` h × ``40.18`` @ ``25%`` markup: two-shot
    ``37.68``, single-shot ``37.67``.
    """
    hours_d = to_decimal_or_none(hours)
    rate_d = to_decimal_or_none(rate)
    if hours_d is None or rate_d is None:
        return None

    markup_d = Decimal(0) if markup is None else to_decimal_or_none(markup)

    cost = round_money(hours_d * rate_d)
    return round_money(cost * (Decimal(1) + markup_d))


def details_ledger_amount(
    price: Any,
    amount: Any,
    *,
    is_credit: bool,
) -> Decimal:
    """Column-N value for a DETAILS worksheet row, signed so a credit reduces the draw.

    The single seam for both halves of a ledger row's money, shared by the
    SharePoint/MS writers (``ExpenseService.sync_to_excel_workbook`` and its
    batch sibling) and the Box row builder
    (``integrations/box/excel/business/row_builder.py``) so the two physical
    copies of one logical ledger can never disagree on a value (KI-39).

    **Price preferred, Amount as fallback.** QBO-pulled account-based lines
    often carry no ``Price``; without the fallback the row lands at ``N = 0``
    and the col-Z idempotency key then freezes it there forever — later
    re-syncs skip it as already-present (KI-16 × KI-46: OHR2-36 carried a
    $18,630 line as $0 in Box long after the underlying Price was corrected).

    **The negation is idempotent BY DESIGN — it fires only on a positive
    value, and must never be "simplified" to an unconditional ``-v``.** Credit
    line amounts are stored positive today, but they are transitioning to
    signed-negative at the write site (the U-344 pattern already underway for
    ``BillCreditLineItem``). An unconditional flip would turn those rows
    positive the moment they land. This is the same landmine, and the same
    guard, as ``entities/invoice/business/cover.py::_signed_line_amount``.

    Always returns a ``Decimal`` — a row with neither value carries ``0``, the
    only thing a ledger cell can mean. Unlike ``to_decimal_or_none`` there is
    no preserve-on-``None`` write downstream to distinguish "absent" from
    "zero", so handing callers an ``Optional`` would only make each of them
    re-decide the same question, and a truthiness guard on a money value is the
    repo's most-repeated bug (``Decimal(0)`` is falsy).
    """
    value = to_decimal_or_none(price)
    if value is None:
        value = to_decimal_or_none(amount)
    if value is None:
        return Decimal(0)
    if is_credit and value > 0:
        value = -value
    return value
