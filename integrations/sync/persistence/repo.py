# Python Standard Library Imports
import base64
import logging
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
