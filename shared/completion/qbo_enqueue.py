"""
Shared QBO push enqueue for completion services (U-797c5a round 2, R1).

Bill and BillCredit both enqueue a QBO outbox row at completion. The row is written with
the realm of the first QBO auth record. Enqueue is NOT gated: ALLOW_QBO_WRITES is enforced at
drain and in the HTTP layer, as for every other external write. A failure here is reported
in the completion result and never raised.

The caller injects `read_auths` (so each service keeps its own, patchable, auth accessor)
and `label` (for the log and message wording, which stays as it was for Bill).
"""

from typing import Callable, Iterable


def enqueue_qbo_push(
    *,
    kind: str,
    entity_type: str,
    entity_public_id: str,
    read_auths: Callable[[], Iterable],
    logger,
    label: str,
) -> dict:
    try:
        auths = read_auths()
        realm_id = auths[0].realm_id if auths else None
        if not realm_id:
            logger.warning(
                f"Skipping QBO enqueue for {label} {entity_public_id}: no QBO auth record configured"
            )
            return {
                "success": False,
                "message": "No QBO auth record configured",
                "qbo_sync_queued": False,
                "errors": [{"step": "qbo_enqueue", "error": "No QBO auth record configured"}],
            }

        from integrations.intuit.qbo.outbox.business.service import QboOutboxService

        outbox_row = QboOutboxService().enqueue(
            kind=kind,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            realm_id=realm_id,
        )
        logger.info(
            f"Enqueued QBO sync for {label} {entity_public_id} "
            f"(outbox {outbox_row.public_id})"
        )
        return {
            "success": True,
            "message": f"QBO push queued (outbox {outbox_row.public_id})",
            "qbo_sync_queued": True,
            "outbox_public_id": outbox_row.public_id,
            "errors": [],
        }
    except Exception as error:
        logger.exception(f"Failed to enqueue QBO sync for {label} {entity_public_id}")
        return {
            "success": False,
            "message": f"Failed to enqueue QBO sync: {error}",
            "qbo_sync_queued": False,
            "errors": [{"step": "qbo_enqueue", "error": str(error)}],
        }
