"""Best-effort compensating rollback when QBO->dbo line projection fails after header create."""
import logging

logger = logging.getLogger(__name__)


def _notify_header_delete_failed(callback, exc, entity_label, entity_id):
    try:
        callback(exc)
    except Exception as cb_exc:
        logger.error(
            f"Compensating rollback: on_header_delete_failed callback failed for "
            f"{entity_label} {entity_id}: {cb_exc}"
        )


def rollback_orphan_header(
    *,
    delete_header,
    delete_mapping,
    entity_label,
    entity_id,
    on_header_delete_failed=None,
):
    """Best-effort compensating delete of a just-created header + its QBO mapping after a
    line-sync failure, so a permanent per-line failure never strands a header-only 'zombie'.

    **Load-bearing delete order (historical):** the mapping had to be deleted before the
    header because qbo.*→dbo.* mapping FKs were NO_ACTION on PurchaseExpense (qbo.BillBill
    never had a FK at all — irrelevant now, but never load-bearing here either).
    VendorCreditBillCredit was retired U-353, PurchaseExpense was retired U-354, and
    BillBill was retired U-355, and InvoiceInvoice — the last header family — was
    retired U-356 (its create-path rollback, `InvoiceInvoiceConnector._stamp_invoice_
    identity`, is a NEW call site of this helper and passes a no-op `delete_mapping`
    too). Every header family's call site now passes a no-op `delete_mapping`, so the
    ordering is moot for all of them; it stays documented because the helper itself
    still honors it for any future mapping-bearing caller. Deleting the header first
    would leave an FK-blocked partial rollback that is worse than the zombie this
    helper prevents.

    Each delete is isolated in its own try/except: failures are LOGGED, never raised, so the
    caller's ORIGINAL line-sync exception propagates unchanged (the pull watermark holds and
    the next idempotent re-pull rebuilds the entity cleanly). delete_header and delete_mapping
    are zero-arg callables supplied by the connector.

    When delete_mapping succeeds but delete_header fails, the orphaned header permanently blocks
    re-pull; on_header_delete_failed (if provided) records a reconciliation issue. When BOTH
    deletes fail the mapping row survives and blocks the header delete (NO_ACTION FK) — both
    rows remain and the next pull re-syncs in place, so the callback must NOT run. The callback
    is itself wrapped in try/except so it can never mask the caller's original error."""
    mapping_deleted = False
    try:
        delete_mapping()
        mapping_deleted = True
    except Exception as e:
        logger.error(
            f"Compensating rollback: failed to delete {entity_label} mapping for {entity_id}: {e}"
        )
    try:
        delete_header()
    except Exception as e:
        if not mapping_deleted:
            logger.error(
                f"Compensating rollback: failed to delete both {entity_label} mapping and "
                f"header {entity_id} (mapping delete failed first; header delete then failed: "
                f"{e}). Both rows survive; the next pull will re-sync in place."
            )
            return
        logger.critical(
            f"Compensating rollback: failed to delete orphan {entity_label} header "
            f"{entity_id} after mapping delete: {e}"
        )
        if on_header_delete_failed is not None:
            _notify_header_delete_failed(on_header_delete_failed, e, entity_label, entity_id)
