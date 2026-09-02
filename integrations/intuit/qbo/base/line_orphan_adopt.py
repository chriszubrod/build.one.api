"""
Shared stale-identity-orphan matcher for the dbo-only line fast path (U-361b).

QBO regenerates a transaction line's `Line.Id` on certain edits — the id changes
("1" -> "2") even when the line's own content is unchanged (documented QBO
behavior; the exact case `_match_unmapped_by_fingerprint`, the with-mapping-era
predecessor of this module, was built for). Under the dbo-only line fast path
(`base/identity_fastpath.py::run_line_identity_fastpath_dbo_only`), a plain
create-only MISS branch treats this as a brand-new line: it mints a fresh dbo
row for the new id and leaves the old dbo row behind, forever unstamped-by-any-
current-id, invisible to every future direct lookup. Two dbo lines then survive
for one QBO line — a silent MONEY DOUBLE-COUNT everywhere the parent's lines are
summed (bill/credit completion totals, invoice draw enrichment), and a stray FK
target for anything that already referenced the orphan's dbo.Id.

This module owns only the MATCHING decision — pure, no I/O, no DB, easily unit-
tested and identical to a fixture dict — so every line family shares the exact
same selection rule instead of hand-copying it (the module docstring in
`base/identity_fastpath.py` explains why hand-copying a fragile invariant is
how a fix to it goes unnoticed). What runs it (`base/identity_fastpath.py`'s
`readopt_candidate` slot) and what happens on a match (apply_fields then
stamp_identity, never a delete-rollback on failure — the row is real,
pre-existing data) both live in the primitive itself; see that module's
docstring for why the distinction matters.
"""

from __future__ import annotations

# Python Standard Library Imports
from typing import Any, Callable, Iterable, Sequence


def find_stale_identity_orphan(
    *,
    existing_lines: Iterable[Any],
    live_qbo_line_ids: frozenset,
    fingerprint: Callable[[Any], Sequence],
    target: Sequence,
) -> Any:
    """
    Find a local line under the same parent whose CURRENT identity is stale
    (its `qbo_id` is not in `live_qbo_line_ids` — the set of QBO line ids this
    pull's parent actually carries right now) and whose content fingerprint
    matches `target`. Returns the FIRST such match in stable position order
    (by `.id`, approximating creation order / original `LineNum`), so several
    unmapped lines sharing a fingerprint (a 50-50 split, repeated draws) pair
    with the QBO lines 1:1 by position instead of both racing for the same
    orphan — mirrors `_match_unmapped_by_fingerprint`'s pre-U-361 selection
    rule exactly, generalized off the mapping-table "unmapped" signal (which
    no longer exists) onto the dbo-only "not currently live" signal instead.

    A line whose `qbo_id` IS in `live_qbo_line_ids` is never a candidate, even
    if its content happens to match `target` — it is correctly bound to a
    DIFFERENT, still-live QBO line elsewhere in this same pull, and stealing
    it would be a genuine identity-theft bug, not a recovery. Only a line
    whose current identity no longer corresponds to anything in this pull is
    "stale" and eligible for re-adoption.

    Returns None when nothing matches — the caller then falls through to a
    fresh create.
    """
    candidates = [
        line
        for line in sorted(existing_lines, key=lambda li: getattr(li, "id", 0) or 0)
        if getattr(line, "qbo_id", None) not in live_qbo_line_ids
        and tuple(fingerprint(line)) == tuple(target)
    ]
    return candidates[0] if candidates else None
