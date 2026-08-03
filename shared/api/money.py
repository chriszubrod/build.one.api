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
