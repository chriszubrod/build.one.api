# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Optional

# Local Imports
from intelligence.outbox.business.model import TimeTrackingOutbox
from intelligence.outbox.persistence.repo import TimeTrackingOutboxRepository

logger = logging.getLogger(__name__)


# Outbox vocabulary --------------------------------------------------------
KIND_REVIEW_SUBMITTED_TIME_ENTRY = "review_submitted_time_entry"
ENTITY_TYPE_TIME_ENTRY = "TimeEntry"


class TimeTrackingOutboxService:
    """
    Service for enqueueing + draining `dbo.TimeTrackingOutbox` rows.

    Enqueue side (called from TimeEntryService.submit):

        TimeTrackingOutboxService().enqueue_review_submitted_time_entry(
            time_entry_public_id=entry.public_id,
        )

    Dedup: if a `pending` or `failed` row already exists for the same
    (EntityType, EntityPublicId, Kind), the existing row is returned —
    no second row is created. Retries and dead-lettering are handled by
    the worker; the producer's job is just "make sure something is queued."

    Worker primitives (claim / mark_done / mark_failed / mark_dead_letter)
    are used by the drain endpoint in Phase 7.
    """

    def __init__(self, repo: Optional[TimeTrackingOutboxRepository] = None):
        self.repo = repo or TimeTrackingOutboxRepository()

    # ------------------------------------------------------------------ #
    # Enqueue
    # ------------------------------------------------------------------ #

    def enqueue_review_submitted_time_entry(
        self,
        *,
        time_entry_public_id: str,
        correlation_id: Optional[str] = None,
    ) -> Optional[TimeTrackingOutbox]:
        """
        Enqueue a `review_submitted_time_entry` outbox row.

        Idempotent via dedup probe — re-enqueuing for the same TimeEntry
        while a pending/failed row exists is a no-op (returns the existing
        row).
        """
        existing = self.repo.read_pending_by_entity(
            entity_type=ENTITY_TYPE_TIME_ENTRY,
            entity_public_id=time_entry_public_id,
            kind=KIND_REVIEW_SUBMITTED_TIME_ENTRY,
        )
        if existing:
            logger.info(
                "time_tracking.outbox.row.coalesced",
                extra={
                    "event_name": "time_tracking.outbox.row.coalesced",
                    "operation_name": KIND_REVIEW_SUBMITTED_TIME_ENTRY,
                    "outbox_public_id": existing.public_id,
                    "entity_type": ENTITY_TYPE_TIME_ENTRY,
                    "entity_public_id": time_entry_public_id,
                    "existing_status": existing.status,
                },
            )
            return existing

        created = self.repo.create(
            kind=KIND_REVIEW_SUBMITTED_TIME_ENTRY,
            entity_type=ENTITY_TYPE_TIME_ENTRY,
            entity_public_id=time_entry_public_id,
            correlation_id=correlation_id,
        )
        logger.info(
            "time_tracking.outbox.row.enqueued",
            extra={
                "event_name": "time_tracking.outbox.row.enqueued",
                "operation_name": KIND_REVIEW_SUBMITTED_TIME_ENTRY,
                "outbox_public_id": created.public_id,
                "entity_type": ENTITY_TYPE_TIME_ENTRY,
                "entity_public_id": time_entry_public_id,
            },
        )
        return created

    # ------------------------------------------------------------------ #
    # Worker primitives (used by the drain endpoint in Phase 7)
    # ------------------------------------------------------------------ #

    def claim_next_pending(self) -> Optional[TimeTrackingOutbox]:
        return self.repo.claim_next_pending()

    def mark_done(self, *, id: int, row_version: str) -> None:
        self.repo.mark_done(id=id, row_version=row_version)

    def mark_failed(
        self,
        *,
        id: int,
        row_version: str,
        next_retry_at: datetime,
        last_error: str,
    ) -> None:
        self.repo.mark_failed(
            id=id,
            row_version=row_version,
            next_retry_at=next_retry_at,
            last_error=last_error,
        )

    def mark_dead_letter(
        self,
        *,
        id: int,
        row_version: str,
        last_error: str,
    ) -> None:
        self.repo.mark_dead_letter(
            id=id,
            row_version=row_version,
            last_error=last_error,
        )
