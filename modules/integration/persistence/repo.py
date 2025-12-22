# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from modules.integration.business.model import Integration
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class IntegrationRepository:
    """
    Repository for Integration persistence operations.
    """

    def __init__(self):
        """Initialize the IntegrationRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Integration]:
        """
        Convert a database row into a Integration dataclass.
        """
        if not row:
            return None

        try:
            return Integration(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                status=row.Status,
                endpoint=row.Endpoint
            )
        except AttributeError as error:
            logger.error(f"Attribute error during integration mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during integration mapping: {error}")
            raise map_database_error(error)

    def create(self, *, name: str, status: str, endpoint: str) -> Integration:
        """
        Create a new integration.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateIntegration",
                    params={
                        "Name": name,
                        "Status": status,
                        "Endpoint": endpoint
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateIntegration did not return a row.")
                    raise map_database_error(Exception("CreateIntegration failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create integration: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Integration]:
        """
        Read all integrations.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadIntegrations",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all integrations: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[Integration]:
        """
        Read a integration by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadIntegrationById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read integration by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Integration]:
        """
        Read a integration by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadIntegrationByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read integration by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Integration]:
        """
        Read a integration by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadIntegrationByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read integration by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, integration: Integration) -> Optional[Integration]:
        """
        Update a integration by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateIntegrationById",
                    params={
                        "Id": integration.id,
                        "RowVersion": integration.row_version_bytes,
                        "Name": integration.name,
                        "Status": integration.status,
                        "Endpoint": integration.endpoint
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update integration by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[Integration]:
        """
        Delete a integration by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteIntegrationById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete integration by ID: {error}")
            raise map_database_error(error)
