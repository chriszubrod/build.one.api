# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.client.business.model import QboClient
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboClientRepository:
    """
    Repository for QboClient persistence operations.
    """

    def __init__(self):
        """Initialize the QboClientRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboClient]:
        """
        Convert a database row into a QboClient dataclass.
        """
        if not row:
            return None

        try:
            return QboClient(
                app=getattr(row, "App", None),
                client_id=getattr(row, "ClientId", None),
                client_secret=getattr(row, "ClientSecret", None),
            )
        except AttributeError as error:
            logger.error("Attribute error during qbo client mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during qbo client mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, app: str, client_id: str, client_secret: str) -> QboClient:
        """
        Create a new QboClient.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateQboClient",
                    params={
                        "App": app,
                        "ClientId": client_id,
                        "ClientSecret": client_secret,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create qbo client did not return a row.")
                    raise map_database_error(Exception("create qbo client failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create qbo client: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[QboClient]:
        """
        Read all QboClients.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboClients",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all qbo clients: %s", error)
            raise map_database_error(error)

    def read_by_app(self, app: str) -> Optional[QboClient]:
        """
        Read a QboClient by app.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboClientByApp",
                    params={
                        "App": app,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read qbo client by app: %s", error)
            raise map_database_error(error)

    def update_by_app(self, app: str, client_id: str, client_secret: str) -> Optional[QboClient]:
        """
        Update a QboClient by app.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateQboClientByApp",
                    params={
                        "App": app,
                        "ClientId": client_id,
                        "ClientSecret": client_secret,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update qbo client did not return a row.")
                    raise map_database_error(Exception("update qbo client by app failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update qbo client by app: %s", error)
            raise map_database_error(error)

    def delete_by_app(self, app: str) -> Optional[QboClient]:
        """
        Delete a QboClient by app.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteQboClientByApp",
                    params={
                        "App": app,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete qbo client did not return a row.")
                    raise map_database_error(Exception("delete qbo client by app failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete qbo client by app: %s", error)
            raise map_database_error(error)
