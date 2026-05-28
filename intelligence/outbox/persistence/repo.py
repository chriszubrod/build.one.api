# Python Standard Library Imports
import base64
import logging
from datetime import datetime
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from intelligence.outbox.business.model import TimeTrackingOutbox
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class TimeTrackingOutboxRepository:
    """Persistence for `[dbo].[TimeTrackingOutbox]`. Calls sprocs in intelligence/outbox/sql/dbo.time_tracking_outbox.sql."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[TimeTrackingOutbox]:
        if not row:
            return None
        try:
            return TimeTrackingOutbox(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                kind=getattr(row, "Kind", None),
                entity_type=getattr(row, "EntityType", None),
                entity_public_id=str(row.EntityPublicId) if getattr(row, "EntityPublicId", None) else None,
                status=getattr(row, "Status", None),
                attempts=getattr(row, "Attempts", None),
                next_retry_at=getattr(row, "NextRetryAt", None),
                ready_after=getattr(row, "ReadyAfter", None),
                last_error=getattr(row, "LastError", None),
                correlation_id=str(row.CorrelationId) if getattr(row, "CorrelationId", None) else None,
                started_at=getattr(row, "StartedAt", None),
                completed_at=getattr(row, "CompletedAt", None),
                dead_lettered_at=getattr(row, "DeadLetteredAt", None),
            )
        except Exception as error:
            logger.error(f"Error mapping TimeTrackingOutbox row: {error}")
            raise map_database_error(error)

    @staticmethod
    def _decode_row_version(row_version: str) -> bytes:
        return base64.b64decode(row_version)

    # --- Create ---

    def create(
        self,
        *,
        kind: str,
        entity_type: str,
        entity_public_id: str,
        ready_after: Optional[datetime] = None,
        correlation_id: Optional[str] = None,
    ) -> TimeTrackingOutbox:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateTimeTrackingOutboxRow",
                        params={
                            "Kind": kind,
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                            "ReadyAfter": ready_after,
                            "CorrelationId": correlation_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create time tracking outbox failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create time tracking outbox: {error}")
            raise map_database_error(error)

    # --- Read ---

    def read_by_id(self, id: int) -> Optional[TimeTrackingOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadTimeTrackingOutboxById", params={"Id": id})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read time tracking outbox by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[TimeTrackingOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadTimeTrackingOutboxByPublicId", params={"PublicId": public_id})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read time tracking outbox by public ID: {error}")
            raise map_database_error(error)

    def read_pending_by_entity(
        self,
        entity_type: str,
        entity_public_id: str,
        kind: str,
    ) -> Optional[TimeTrackingOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPendingTimeTrackingOutboxByEntity",
                        params={
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                            "Kind": kind,
                        },
                    )
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read pending time tracking outbox by entity: {error}")
            raise map_database_error(error)

    # --- Worker transitions ---

    def claim_next_pending(self) -> Optional[TimeTrackingOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ClaimNextPendingTimeTrackingOutbox", params={})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during claim next pending time tracking outbox: {error}")
            raise map_database_error(error)

    def mark_done(self, id: int, row_version: str) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CompleteTimeTrackingOutbox",
                        params={"Id": id, "RowVersion": self._decode_row_version(row_version)},
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during mark time tracking outbox done: {error}")
            raise map_database_error(error)

    def mark_failed(
        self,
        id: int,
        row_version: str,
        next_retry_at: datetime,
        last_error: str,
    ) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="FailTimeTrackingOutbox",
                        params={
                            "Id": id,
                            "RowVersion": self._decode_row_version(row_version),
                            "NextRetryAt": next_retry_at,
                            "LastError": last_error,
                        },
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during mark time tracking outbox failed: {error}")
            raise map_database_error(error)

    def mark_dead_letter(
        self,
        id: int,
        row_version: str,
        last_error: str,
    ) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeadLetterTimeTrackingOutbox",
                        params={
                            "Id": id,
                            "RowVersion": self._decode_row_version(row_version),
                            "LastError": last_error,
                        },
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during mark time tracking outbox dead letter: {error}")
            raise map_database_error(error)
