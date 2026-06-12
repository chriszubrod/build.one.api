# Python Standard Library Imports
import base64
import logging
from datetime import datetime
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.box.outbox.business.model import BoxOutbox
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BoxOutboxRepository:
    """Persistence for `[box].[Outbox]`. Calls sprocs in outbox/sql/box.outbox.sql."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BoxOutbox]:
        if not row:
            return None
        try:
            return BoxOutbox(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                kind=getattr(row, "Kind", None),
                entity_type=getattr(row, "EntityType", None),
                entity_public_id=str(row.EntityPublicId) if getattr(row, "EntityPublicId", None) else None,
                request_id=str(row.RequestId) if getattr(row, "RequestId", None) else None,
                payload=getattr(row, "Payload", None),
                status=getattr(row, "Status", None),
                attempts=getattr(row, "Attempts", None),
                next_retry_at=getattr(row, "NextRetryAt", None),
                ready_after=getattr(row, "ReadyAfter", None),
                last_error=getattr(row, "LastError", None),
                correlation_id=str(row.CorrelationId) if getattr(row, "CorrelationId", None) else None,
                created_by_user_id=getattr(row, "CreatedByUserId", None),
                started_at=getattr(row, "StartedAt", None),
                completed_at=getattr(row, "CompletedAt", None),
                dead_lettered_at=getattr(row, "DeadLetteredAt", None),
            )
        except Exception as error:
            logger.error(f"Error mapping BoxOutbox row: {error}")
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
        request_id: str,
        payload: Optional[str] = None,
        ready_after: Optional[datetime] = None,
        correlation_id: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> BoxOutbox:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBoxOutbox",
                        params={
                            "Kind": kind,
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                            "RequestId": request_id,
                            "Payload": payload,
                            "ReadyAfter": ready_after,
                            "CorrelationId": correlation_id,
                            "CreatedByUserId": created_by_user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create box outbox failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create box outbox: {error}")
            raise map_database_error(error)

    # --- Read ---

    def read_by_id(self, id: int) -> Optional[BoxOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadBoxOutboxById", params={"Id": id})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box outbox by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[BoxOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadBoxOutboxByPublicId", params={"PublicId": public_id})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box outbox by public ID: {error}")
            raise map_database_error(error)

    def read_pending_by_entity(
        self,
        entity_type: str,
        entity_public_id: str,
        kind: str,
    ) -> List[BoxOutbox]:
        """
        All `pending`/`failed` rows for an (entity_type, entity_public_id, kind)
        triple. Returns a list — one entity can have several pending uploads
        (one per attachment); Policy C coalescing inspects each row's payload
        to find the one whose `attachment_id` matches.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPendingBoxOutboxByEntity",
                        params={
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                            "Kind": kind,
                        },
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(r) for r in rows if r]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read pending box outbox by entity: {error}")
            raise map_database_error(error)

    # --- Update ---

    def update_ready_after(self, id: int, row_version: str, ready_after: datetime) -> Optional[BoxOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateBoxOutboxReadyAfter",
                        params={
                            "Id": id,
                            "ReadyAfter": ready_after,
                            "RowVersion": self._decode_row_version(row_version),
                        },
                    )
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update box outbox ready_after: {error}")
            raise map_database_error(error)

    def update_payload(self, id: int, row_version: str, payload: str) -> Optional[BoxOutbox]:
        """Update the Payload JSON on an in-flight outbox row."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateBoxOutboxPayload",
                        params={
                            "Id": id,
                            "Payload": payload,
                            "RowVersion": self._decode_row_version(row_version),
                        },
                    )
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update box outbox payload: {error}")
            raise map_database_error(error)

    # --- Worker transitions ---

    def claim_next_pending(self) -> Optional[BoxOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ClaimNextPendingBoxOutbox", params={})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during claim next pending box outbox: {error}")
            raise map_database_error(error)

    def mark_done(self, id: int, row_version: str) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CompleteBoxOutbox",
                        params={"Id": id, "RowVersion": self._decode_row_version(row_version)},
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during mark box outbox done: {error}")
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
                        name="FailBoxOutbox",
                        params={
                            "Id": id,
                            "LastError": last_error,
                            "NextRetryAt": next_retry_at,
                            "RowVersion": self._decode_row_version(row_version),
                        },
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during mark box outbox failed: {error}")
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
                        name="DeadLetterBoxOutbox",
                        params={
                            "Id": id,
                            "LastError": last_error,
                            "RowVersion": self._decode_row_version(row_version),
                        },
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during mark box outbox dead_letter: {error}")
            raise map_database_error(error)
