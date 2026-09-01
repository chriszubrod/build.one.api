"""Clears an entity's own qbo.* mapping row when the entity itself is being deleted.

Entity delete services (Bill, BillCredit, Expense) hand-cascade their own children but,
absent this, never clear the qbo.* mapping row that points back at their own header row —
leaving a permanent dangling mapping, or (where the mapping FK is NO_ACTION rather than
CASCADE in prod — PurchaseExpense) a SQL 547 on the header delete itself. If the header
delete fails after the mapping is cleared, the mapping is best-effort restored so a
failed attempt never unmaps a still-live entity. See U-226. (BillCredit's own
VendorCreditBillCredit mapping was retired U-353 — its delete no longer calls this
helper at all; dbo.BillCredit.QboId/RealmId are plain columns that die with the row.)

Also used for line-item mapping cleanup (BillLineItem, InvoiceLineItem, ExpenseLineItem,
U-241) — the mechanism does not distinguish header vs line item; it clears one qbo.*
mapping row before deleting the row it points at."""
import logging

from integrations.intuit.qbo.base.locking import qbo_app_lock

logger = logging.getLogger(__name__)


def make_restore_failed_recorder(
    *,
    entity_type,
    mapping_label,
    staging_repo_factory,
    qbo_id_from_mapping,
    local_id,
):
    """Factory for delete_own_qbo_mapping_before_header's on_restore_failed hook — records a durable qbo.ReconciliationIssue for the mapping-permanently-lost state. Never masks the original header-delete exception (the caller re-raises it unconditionally after invoking this)."""
    def _on_mapping_restore_failed(mapping, restore_exc):
        try:
            from integrations.intuit.qbo.base.delete_reconcile import record_partial_delete_issue

            staging = staging_repo_factory().read_by_id(qbo_id_from_mapping(mapping))
            record_partial_delete_issue(
                entity_type=entity_type,
                mapping_label=mapping_label,
                mapped_label=entity_type,
                realm_id=staging.realm_id if staging else "",
                qbo_id=staging.qbo_id if staging else "",
                local_id=local_id,
                error=restore_exc,
            )
        except Exception:
            logger.exception(
                f"Failed to record partial-delete reconciliation issue for {entity_type} {local_id}"
            )

    return _on_mapping_restore_failed


def delete_own_qbo_mapping_before_header(
    *,
    read_mapping,
    delete_mapping,
    recreate_mapping,
    delete_header,
    entity_label,
    entity_id,
    on_restore_failed=None,
):
    """Delete a header entity's own qbo.* mapping row (if any), then delete the header
    itself.

    If a mapping is found and successfully deleted, but delete_header() then raises, the
    mapping is best-effort RESTORED via recreate_mapping so a failed delete attempt never
    leaves a still-live, QBO-synced entity permanently unmapped (which would risk a
    duplicate on the next QBO pull). The original delete_header() exception always
    propagates unchanged — restoration is a side effect, not a swallow. See U-226.

    The entire operation runs under a per-entity sp_getapplock keyed
    ``qbo_mapping_delete:{entity_label}:{entity_id}`` so concurrent deletes of the same
    entity cannot interleave mapping removal and compensating restore.

    read_mapping: zero-arg callable -> the mapping row, or falsy/None if unmapped.
    delete_mapping: one-arg callable(mapping) -> deletes it. Raises are fatal (see below).
    recreate_mapping: one-arg callable(mapping) -> best-effort re-creates the SAME mapping
        row; only invoked if delete_header() raises after delete_mapping() succeeded.
    delete_header: zero-arg callable -> deletes the header row; return value is passed
        through unchanged on success.
    entity_label / entity_id: for log/error messages.
    on_restore_failed: optional two-arg callable(mapping, restore_exc) invoked, best-effort,
        only when recreate_mapping() itself raises after a header-delete failure — for durably
        recording the "mapping permanently lost" state (e.g. via qbo.ReconciliationIssue).
        Never allowed to mask the original header-delete exception, which always still
        propagates. Header call sites (Bill/BillCredit/Expense/Invoice) wire this to record a
        qbo.ReconciliationIssue. Line-item call sites (BillLineItem/InvoiceLineItem/
        ExpenseLineItem, U-241) currently pass None — resolving a line mapping's realm_id/
        qbo_id needs an extra hop through its parent staging row that no line-item call site
        has wired yet; the logger.critical above still captures the double-failure for manual
        reconciliation either way.

    Raises ValueError (chaining the original exception) if reading or deleting a found
    mapping fails — same behavior as before, the header delete is never attempted in that
    case (an unclearable mapping would 547 the header anyway, or silently orphan it).
    """
    with qbo_app_lock(f"qbo_mapping_delete:{entity_label}:{entity_id}") as got_lock:
        if not got_lock:
            raise ValueError(
                f"Cannot delete {entity_label}: could not acquire mapping-cleanup lock for "
                f"{entity_id} (a concurrent delete may be in progress)"
            )

        try:
            mapping = read_mapping()
        except Exception as e:
            logger.error(f"Error reading qbo mapping for {entity_label} {entity_id}: {e}")
            raise ValueError(
                f"Cannot delete {entity_label}: failed to read qbo mapping for {entity_id}"
            ) from e

        if not mapping:
            return delete_header()

        try:
            delete_mapping(mapping)
            logger.info(f"Deleted qbo mapping for {entity_label} {entity_id}")
        except Exception as e:
            logger.error(f"Error deleting qbo mapping for {entity_label} {entity_id}: {e}")
            raise ValueError(
                f"Cannot delete {entity_label}: failed to delete qbo mapping for {entity_id}"
            ) from e

        try:
            return delete_header()
        except Exception as header_exc:
            try:
                recreate_mapping(mapping)
                logger.error(
                    f"Header delete failed for {entity_label} {entity_id} after its qbo "
                    f"mapping was already deleted; mapping RESTORED so the still-live entity "
                    f"stays mapped: {header_exc}"
                )
            except Exception as restore_exc:
                logger.critical(
                    f"Header delete failed for {entity_label} {entity_id} after its qbo "
                    f"mapping was already deleted, AND restoring the mapping ALSO failed — "
                    f"{entity_label} {entity_id} is now a live, QBO-synced entity with NO "
                    f"mapping row. Needs manual reconciliation. header_exc={header_exc} "
                    f"restore_exc={restore_exc}"
                )
                if on_restore_failed is not None:
                    try:
                        on_restore_failed(mapping, restore_exc)
                    except Exception as callback_exc:
                        logger.error(
                            f"on_restore_failed callback itself failed for {entity_label} "
                            f"{entity_id}: {callback_exc}"
                        )
            raise
