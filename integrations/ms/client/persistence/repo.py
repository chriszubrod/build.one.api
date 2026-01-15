# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.client.business.model import MsClient
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)

class MsClientRepository:
    """
    Repository for MsClient persistence operations.
    """

    def __init__(self):
        """Initialize the MsClientRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsClient]:
        """
        Convert a database row into a MsClient dataclass.
        """
        if not row:
            return None

        try:
            return MsClient(
                app=getattr(row, "App", None),
                client_id=getattr(row, "ClientId", None),
                client_secret=getattr(row, "ClientSecret", None),
                tenant_id=getattr(row, "TenantId", None),
                redirect_uri=getattr(row, "RedirectUri", None)
            )
        except AttributeError as error:
            logger.error("Attribute error during ms client mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during ms client mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, app: str, client_id: str, client_secret: str, tenant_id: str, redirect_uri: str) -> MsClient:
        """
        Create a new MsClient.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsClient",
                    params={
                        "App": app,
                        "ClientId": client_id,
                        "ClientSecret": client_secret,
                        "TenantId": tenant_id,
                        "RedirectUri": redirect_uri,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create ms client did not return a row.")
                    raise map_database_error(Exception("create ms client failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create ms client: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[MsClient]:
        """
        Read all MsClients.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsClients",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all ms clients: %s", error)
            raise map_database_error(error)

    def read_by_app(self, app: str) -> Optional[MsClient]:
        """
        Read a MsClient by app.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsClientByApp",
                    params={
                        "App": app,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read ms client by app: %s", error)
            raise map_database_error(error)

    def update_by_app(self, app: str, client_id: str, client_secret: str, tenant_id: str, redirect_uri: str) -> Optional[MsClient]:
        """
        Update a MsClient by app.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateMsClientByApp",
                    params={
                        "App": app,
                        "ClientId": client_id,
                        "ClientSecret": client_secret,
                        "TenantId": tenant_id,
                        "RedirectUri": redirect_uri,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update ms client did not return a row.")
                    raise map_database_error(Exception("update ms client by app failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update ms client by app: %s", error)
            raise map_database_error(error)

    def delete_by_app(self, app: str) -> Optional[MsClient]:
        """
        Delete a MsClient by app.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteMsClientByApp",
                    params={
                        "App": app,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete ms client did not return a row.")
                    raise map_database_error(Exception("delete ms client by app failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete ms client by app: %s", error)
            raise map_database_error(error)
