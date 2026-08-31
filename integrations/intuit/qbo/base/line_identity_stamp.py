"""Shared invariant for QBO line connectors: stamp dbo line identity ONLY when
the mapping create actually succeeded (U-341, U-339 follow-up).

The 4 QBO line connectors (bill/vendorcredit/invoice/purchase line items) each
hand-roll a try/except/else around their CREATE-branch `create_mapping()` call
to keep `stamp_line_identity_or_warn` unreachable on a mapping failure — it
already shipped broken once (`bill_line_item`, U-339: a swallowed ValueError
still stamped identity, producing a "stamped-but-unmapped" row neither the
fast path nor Shape-B's fingerprint can recover). `create_mapping_then_stamp`
makes that unreachability structural instead of conventional: there is no
code path from a failed `create_mapping` to `stamp_identity`.

The 4 connectors deliberately do NOT share one failure policy (see
`docs/design/u341-create-mapping-then-stamp.md` §4, `/em` Gate-2) — bill
warns-and-skips on `ValueError` only (a `DatabaseConstraintError` race still
propagates); bill_credit warns-and-skips on any `Exception`; invoice and
purchase compensating-delete the orphaned line and re-raise. `catch` and
`on_mapping_failure` carry that per-connector policy through unchanged.
`catch` has no default — every call site must state its own policy explicitly.
"""

from __future__ import annotations

from typing import Any, Callable


def create_mapping_then_stamp(
    *,
    create_mapping: Callable[[], Any],
    stamp_identity: Callable[[], None],
    on_mapping_failure: Callable[[Exception], None],
    catch: tuple,
) -> None:
    """Run `create_mapping()`; call `stamp_identity()` only if it didn't raise.

    On a `catch`-matching failure, delegates to `on_mapping_failure(exc)` —
    the connector's own policy, which may warn and return (skip the stamp) or
    re-raise (abort the line). Either way `stamp_identity` is never called.
    """
    try:
        create_mapping()
    except catch as exc:
        on_mapping_failure(exc)
        return
    stamp_identity()
