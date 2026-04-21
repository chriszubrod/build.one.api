# Python Standard Library Imports
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

# Local Imports
from integrations.intuit.qbo.base.correlation import get_correlation_id
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.persistence.repo import QboOutboxRepository

logger = logging.getLogger(__name__)


# Policy C debounce window. Edits to the same entity within this window
# after the last edit collapse into a single outbox row. 5s is the value
# agreed in Chapter 2 — short enough to feel responsive to the user, long
# enough to absorb normal typing / auto-save cadence.
DEFAULT_DEBOUNCE_SECONDS = 5


class QboOutboxService:
    """
    Service for enqueueing QBO write operations into the durable outbox.

    Single public surface:

        outbox_service.enqueue(
            kind="sync_bill_to_qbo",
            entity_type="Bill",
            entity_public_id=bill.public_id,
            realm_id=realm_id,
        )

    Callers invoke this from inside the same local transaction that mutates
    the entity. The worker (task #14d) drains and dispatches later.
    """

    def __init__(self, repo: Optional[QboOutboxRepository] = None):
        self.repo = repo or QboOutboxRepository()

    def enqueue(
        self,
        *,
        kind: str,
        entity_type: str,
        entity_public_id: str,
        realm_id: str,
        debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS,
    ) -> QboOutbox:
        """
        Enqueue a QBO write operation.

        Policy C coalesce: if a pending or failed outbox row already exists
        for this `(entity_type, entity_public_id, kind)`, extend its
        `ReadyAfter` to `now + debounce_seconds` instead of creating a new
        row. Multiple edits in quick succession collapse into one outbox
        record whose payload is re-read from the entity table at drain time.

        Args:
            kind: Operation identifier, e.g. "sync_bill_to_qbo".
            entity_type: Local entity type — "Bill" | "Expense" | "Invoice" | "BillCredit".
            entity_public_id: public_id of the local entity.
            realm_id: QBO realm the operation targets.
            debounce_seconds: Policy C window; default 5s.

        Returns:
            The outbox row (either newly created or the coalesced existing one).
        """
        now = datetime.now(timezone.utc)
        ready_after = now + timedelta(seconds=debounce_seconds)
        correlation_id = get_correlation_id()

        # Policy C: look for an existing pending/failed row for this entity+kind.
        existing = self.repo.read_pending_by_entity(
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            kind=kind,
        )

        if existing:
            # Coalesce into the existing row: extend ReadyAfter to push the
            # drain out. We don't touch NextRetryAt (so any retry-backoff in
            # progress is preserved) and we don't touch RequestId (so retries
            # continue to use the same idempotency key QBO knows).
            updated = self.repo.update_ready_after(
                id=existing.id,
                row_version=existing.row_version,
                ready_after=ready_after,
            )
            logger.info(
                "qbo.outbox.row.coalesced",
                extra={
                    "event_name": "qbo.outbox.row.coalesced",
                    "correlation_id": correlation_id,
                    "operation_name": kind,
                    "realm_id": realm_id,
                    "outbox_public_id": existing.public_id,
                    "entity_type": entity_type,
                    "entity_public_id": entity_public_id,
                    "new_ready_after": ready_after.isoformat(),
                },
            )
            return updated or existing

        # No existing row; create fresh.
        request_id = str(uuid.uuid4())
        created = self.repo.create(
            kind=kind,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            realm_id=realm_id,
            request_id=request_id,
            ready_after=ready_after,
            correlation_id=correlation_id,
        )
        logger.info(
            "qbo.outbox.row.enqueued",
            extra={
                "event_name": "qbo.outbox.row.enqueued",
                "correlation_id": correlation_id,
                "operation_name": kind,
                "realm_id": realm_id,
                "outbox_public_id": created.public_id,
                "entity_type": entity_type,
                "entity_public_id": entity_public_id,
                "request_id": request_id,
                "ready_after": ready_after.isoformat(),
            },
        )
        return created
