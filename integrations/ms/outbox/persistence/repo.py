# Python Standard Library Imports
import base64
import logging
from datetime import datetime
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.outbox.business.model import MsOutbox
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class MsOutboxRepository:
    """Persistence for `[ms].[Outbox]`. Calls sprocs in outbox/sql/ms.outbox.sql."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsOutbox]:
        if not row:
            return None
        try:
            return MsOutbox(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                kind=getattr(row, "Kind", None),
                entity_type=getattr(row, "EntityType", None),
                entity_public_id=str(row.EntityPublicId) if getattr(row, "EntityPublicId", None) else None,
                tenant_id=getattr(row, "TenantId", None),
                request_id=str(row.RequestId) if getattr(row, "RequestId", None) else None,
                payload=getattr(row, "Payload", None),
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
            logger.error(f"Error mapping MsOutbox row: {error}")
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
        tenant_id: str,
        request_id: str,
        payload: Optional[str] = None,
        ready_after: Optional[datetime] = None,
        correlation_id: Optional[str] = None,
    ) -> MsOutbox:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateMsOutbox",
                        params={
                            "Kind": kind,
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                            "TenantId": tenant_id,
                            "RequestId": request_id,
                            "Payload": payload,
                            "ReadyAfter": ready_after,
                            "CorrelationId": correlation_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create ms outbox failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create ms outbox: {error}")
            raise map_database_error(error)

    # --- Read ---

    def read_by_id(self, id: int) -> Optional[MsOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadMsOutboxById", params={"Id": id})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read ms outbox by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadMsOutboxByPublicId", params={"PublicId": public_id})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read ms outbox by public ID: {error}")
            raise map_database_error(error)

    def read_pending_by_entity(
        self,
        entity_type: str,
        entity_public_id: str,
        kind: str,
    ) -> Optional[MsOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPendingMsOutboxByEntity",
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
            logger.error(f"Error during read pending ms outbox by entity: {error}")
            raise map_database_error(error)

    # --- Update ---

    def update_ready_after(self, id: int, row_version: str, ready_after: datetime) -> Optional[MsOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateMsOutboxReadyAfter",
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
            logger.error(f"Error during update ms outbox ready_after: {error}")
            raise map_database_error(error)

    def update_payload(self, id: int, row_version: str, payload: str) -> Optional[MsOutbox]:
        """Update the Payload JSON on an in-flight outbox row (resumable upload)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateMsOutboxPayload",
                        params={
                            "Id": id,
                            "RowVersion": self._decode_row_version(row_version),
                            "Payload": payload,
                        },
                    )
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update ms outbox payload: {error}")
            raise map_database_error(error)

    # --- Worker transitions ---

    def claim_next_pending(self) -> Optional[MsOutbox]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ClaimNextPendingMsOutbox", params={})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during claim next pending ms outbox: {error}")
            raise map_database_error(error)

    def mark_done(self, id: int, row_version: str) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CompleteMsOutbox",
                        params={"Id": id, "RowVersion": self._decode_row_version(row_version)},
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during mark ms outbox done: {error}")
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
                        name="FailMsOutbox",
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
            logger.error(f"Error during mark ms outbox failed: {error}")
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
                        name="DeadLetterMsOutbox",
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
            logger.error(f"Error during mark ms outbox dead_letter: {error}")
            raise map_database_error(error)
