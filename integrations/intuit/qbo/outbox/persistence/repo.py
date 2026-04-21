# Python Standard Library Imports
import base64
import logging
from datetime import datetime
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboOutboxRepository:
    """
    Persistence for `[qbo].[Outbox]`. Calls into stored procedures defined
    in `outbox/sql/qbo.outbox.sql`.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboOutbox]:
        if not row:
            return None
        try:
            return QboOutbox(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                kind=getattr(row, "Kind", None),
                entity_type=getattr(row, "EntityType", None),
                entity_public_id=str(row.EntityPublicId) if getattr(row, "EntityPublicId", None) else None,
                realm_id=getattr(row, "RealmId", None),
                request_id=str(row.RequestId) if getattr(row, "RequestId", None) else None,
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
            logger.error(f"Error mapping QboOutbox row: {error}")
            raise map_database_error(error)

    @staticmethod
    def _decode_row_version(row_version: str) -> bytes:
        """Base64 → bytes for SQL Server ROWVERSION optimistic-lock params."""
        return base64.b64decode(row_version)

    # ------------------------------------------------------------------ #
    # Create
    # ------------------------------------------------------------------ #

    def create(
        self,
        *,
        kind: str,
        entity_type: str,
        entity_public_id: str,
        realm_id: str,
        request_id: str,
        ready_after: Optional[datetime] = None,
        correlation_id: Optional[str] = None,
    ) -> QboOutbox:
        """Insert a pending outbox row."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboOutbox",
                        params={
                            "Kind": kind,
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                            "RealmId": realm_id,
                            "RequestId": request_id,
                            "ReadyAfter": ready_after,
                            "CorrelationId": correlation_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create qbo outbox failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo outbox: {error}")
            raise map_database_error(error)

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #

    def read_by_id(self, id: int) -> Optional[QboOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadQboOutboxById", params={"Id": id})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo outbox by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[QboOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadQboOutboxByPublicId", params={"PublicId": public_id})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo outbox by public ID: {error}")
            raise map_database_error(error)

    def read_pending_by_entity(
        self,
        entity_type: str,
        entity_public_id: str,
        kind: str,
    ) -> Optional[QboOutbox]:
        """
        Find the most recent pending/failed outbox row for this entity+kind.
        Used by the service layer for Policy C coalesce decisions.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPendingQboOutboxByEntity",
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
            logger.error(f"Error during read pending qbo outbox by entity: {error}")
            raise map_database_error(error)

    # ------------------------------------------------------------------ #
    # Update
    # ------------------------------------------------------------------ #

    def update_ready_after(self, id: int, row_version: str, ready_after: datetime) -> Optional[QboOutbox]:
        """Extend the Policy C debounce window on an existing row."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboOutboxReadyAfter",
                        params={
                            "Id": id,
                            "RowVersion": self._decode_row_version(row_version),
                            "ReadyAfter": ready_after,
                        },
                    )
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo outbox ready_after: {error}")
            raise map_database_error(error)

    # ------------------------------------------------------------------ #
    # Worker transitions (used by 14d — included now so the SQL side is complete)
    # ------------------------------------------------------------------ #

    def claim_next_pending(self) -> Optional[QboOutbox]:
        """Atomically claim the oldest drain-ready row. Returns None if nothing ready."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ClaimNextPendingQboOutbox", params={})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during claim next pending qbo outbox: {error}")
            raise map_database_error(error)

    def mark_done(self, id: int, row_version: str) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CompleteQboOutbox",
                        params={"Id": id, "RowVersion": self._decode_row_version(row_version)},
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during mark qbo outbox done: {error}")
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
                        name="FailQboOutbox",
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
            logger.error(f"Error during mark qbo outbox failed: {error}")
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
                        name="DeadLetterQboOutbox",
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
            logger.error(f"Error during mark qbo outbox dead_letter: {error}")
            raise map_database_error(error)
