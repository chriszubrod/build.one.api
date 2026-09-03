"""Shared orphan-line ReconciliationIssue recorders for the dbo-only line
identity fast path (base/identity_fastpath.py::run_line_identity_fastpath_dbo_only).

Every line family cloning that primitive (bill_credit_line_item U-361,
invoice_line_item U-362, bill_line_item U-363, ...) wires the SAME three
failure-recording shapes into the primitive's `on_readopt_stamp_failed` /
`on_create_failed` callbacks and its `rollback_candidate`'s own failure path.
TODO.md's U-361 follow-ups flagged `_record_orphan_line_issue` as the 6th
hand-copy of this shape (U-362 made it the 6th, against that note's own prior
instruction not to) and marked lifting it a HARD PREREQUISITE for U-363 —
this module is that extraction. U-361/U-362's own connectors are repointed
onto it in the same unit that adds it (bill_line_item, U-363), not left as
a 4th/5th/6th copy.
"""
from typing import Any, Optional

from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue


def record_orphan_line_issue(
    repo,
    *,
    drift_type: str,
    entity_type: str,
    line_item: Any,
    qbo_line_id: Optional[str],
    parent_label: str,
    parent_id: Any,
    realm_id: Optional[str],
    exc: Exception,
) -> None:
    """`on_header_delete_failed` for the line-level use of `rollback_orphan_header`
    (U-354/U-355's identity-stamp rollback, at line level): a stamp failure's
    compensating delete of the just-created, unstamped line itself failed.
    The orphan is invisible to the dbo-native fast path, so every re-pull
    will mint a duplicate line until it is deleted or stamped by hand."""
    record_mapping_issue(
        repo,
        drift_type=drift_type,
        entity_type=entity_type,
        entity_public_id=str(line_item.public_id) if getattr(line_item, "public_id", None) else None,
        qbo_id=str(qbo_line_id) if qbo_line_id else None,
        realm_id=realm_id or "",
        details=(
            f"Compensating rollback failed to delete unstamped {entity_type} "
            f"{line_item.id} ({getattr(line_item, 'public_id', None)}) on {parent_label} "
            f"{parent_id} after its identity stamp for QBO line {qbo_line_id} failed: "
            f"{exc}. The orphan is invisible to the dbo-native fast path, so every "
            f"re-pull will mint a duplicate line until it is deleted or stamped by hand."
        ),
    )


def record_readopt_stamp_failed_issue(
    repo,
    *,
    drift_type: str,
    entity_type: str,
    line_item: Any,
    qbo_line_id: Optional[str],
    parent_label: str,
    parent_id: Any,
    realm_id: Optional[str],
    exc: Exception,
) -> None:
    """`on_readopt_stamp_failed` (U-361b shape): a stale-identity orphan was
    found and matched, but re-applying/re-stamping it failed. NOTHING is
    deleted — the row stays exactly as it was, under its OLD identity.
    Recorded so a human knows this parent will keep re-adopting on retry
    rather than silently double-counting forever."""
    record_mapping_issue(
        repo,
        drift_type=drift_type,
        entity_type=entity_type,
        entity_public_id=str(line_item.public_id) if getattr(line_item, "public_id", None) else None,
        qbo_id=str(qbo_line_id) if qbo_line_id else None,
        realm_id=realm_id or "",
        details=(
            f"Found a stale-identity orphan {entity_type} {line_item.id} "
            f"({getattr(line_item, 'public_id', None)}) on {parent_label} {parent_id} "
            f"matching QBO line {qbo_line_id} by content fingerprint, but re-adopting "
            f"it failed: {exc}. The row was left UNTOUCHED under its previous identity "
            f"(never deleted) - this {parent_label.lower()} will keep retrying the "
            f"readopt on every re-pull until it succeeds or is resolved by hand."
        ),
    )


def record_create_failed_issue(
    repo,
    *,
    drift_type: str,
    entity_type: str,
    qbo_line_id: Optional[str],
    parent_label: str,
    parent_id: Any,
    realm_id: Optional[str],
    exc: Exception,
) -> None:
    """`on_create_failed` (U-361b P2 hardening shape): `resolve_candidate`
    (the fresh-create path) raised. If the underlying INSERT actually
    committed before the failure, there is no candidate reference to
    identify or delete - this is only a DETECTABILITY signal, not a claim
    that a row exists."""
    record_mapping_issue(
        repo,
        drift_type=drift_type,
        entity_type=entity_type,
        entity_public_id=None,
        qbo_id=str(qbo_line_id) if qbo_line_id else None,
        realm_id=realm_id or "",
        details=(
            f"Creating a new {entity_type} for QBO line {qbo_line_id} on {parent_label} "
            f"{parent_id} failed: {exc}. If the underlying write actually committed "
            f"before this failure, an unstamped (QboId IS NULL) orphan may exist under "
            f"this {parent_label} - not confirmed by this record alone, but worth a "
            f"manual check."
        ),
    )
