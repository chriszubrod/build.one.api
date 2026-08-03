# Python Standard Library Imports
from decimal import Decimal
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
