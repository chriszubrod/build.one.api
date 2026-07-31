# Python Standard Library Imports
import base64
import logging
from datetime import datetime, timezone
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.sync.business.model import Sync
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _parse_sync_last_sync(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _sync_row_is_newer(candidate: Sync, incumbent: Sync) -> bool:
    c_ts = _parse_sync_last_sync(candidate.last_sync_datetime) or _EPOCH
    i_ts = _parse_sync_last_sync(incumbent.last_sync_datetime) or _EPOCH
    if c_ts != i_ts:
        return c_ts > i_ts
    # Deterministic tie-breaker on equal LastSyncDatetime (e.g. two Env rows for
    # the same (provider, entity)): prefer the higher Id (most recently inserted).
    # Immaterial to the freshness verdict — the ages are identical — but keeps the
    # selection stable regardless of ReadSyncs' Provider-only ordering.
    return (candidate.id or 0) > (incumbent.id or 0)


class SyncRepository:
    """
    Repository for Sync persistence operations.
    """

    def __init__(self):
        """Initialize the SyncRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Sync]:
        """
        Convert a database row into a Sync dataclass.
        """
        if not row:
            return None

        try:
            return Sync(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                provider=row.Provider,
                env=row.Env,
                entity=row.Entity,
                last_sync_datetime=row.LastSyncDatetime
            )
        except AttributeError as error:
            logger.error(f"Attribute error during sync mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during sync mapping: {error}")
            raise map_database_error(error)

    def create(self, *, provider: Optional[str], env: Optional[str], entity: Optional[str], last_sync_datetime: Optional[str]) -> Sync:
        """
        Create a new sync record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateSync",
                    params={
                        "Provider": provider,
                        "Env": env,
                        "Entity": entity,
                        "LastSyncDatetime": last_sync_datetime,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateSync did not return a row.")
                    raise map_database_error(Exception("CreateSync failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create sync: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Sync]:
        """
        Read all sync records.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSyncs",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all syncs: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[Sync]:
        """
        Read a sync record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSyncById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read sync by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Sync]:
        """
        Read a sync record by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSyncByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read sync by public ID: {error}")
            raise map_database_error(error)

    def read_by_provider(self, provider: str) -> Optional[Sync]:
        """
        Read a sync record by provider.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSyncByProvider",
                    params={"Provider": provider},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read sync by provider: {error}")
            raise map_database_error(error)

    _QBO_PULL_ENTITIES = frozenset({"bill", "invoice", "purchase", "vendorcredit"})

    def read_qbo_pull_watermarks(self) -> list[Sync]:
        """Read-only: dbo.Sync rows for QBO entity pulls (Step 2a freshness)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSyncs",
                    params={},
                )
                rows = cursor.fetchall()
                records = [self._from_db(row) for row in rows if row]
                filtered = [
                    r
                    for r in records
                    if r
                    and (r.provider or "").lower() == "qbo"
                    and (r.entity or "").lower() in self._QBO_PULL_ENTITIES
                ]
                by_entity: dict[str, Sync] = {}
                for rec in filtered:
                    entity = (rec.entity or "").lower()
                    prev = by_entity.get(entity)
                    if prev is None or _sync_row_is_newer(rec, prev):
                        by_entity[entity] = rec
                return list(by_entity.values())
        except Exception as error:
            logger.error(f"Error during read qbo pull watermarks: {error}")
            raise map_database_error(error)

    def update_by_id(self, sync: Sync) -> Optional[Sync]:
        """
        Update a sync record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateSyncById",
                    params={
                        "Id": sync.id,
                        "RowVersion": sync.row_version_bytes,
                        "Provider": sync.provider,
                        "Env": sync.env,
                        "Entity": sync.entity,
                        "LastSyncDatetime": sync.last_sync_datetime,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update sync by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[Sync]:
        """
        Delete a sync record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteSyncById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete sync by ID: {error}")
            raise map_database_error(error)
