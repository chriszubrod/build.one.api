# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.site.business.model import MsSite
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class MsSiteRepository:
    """
    Repository for MsSite persistence operations.
    """

    def __init__(self):
        """Initialize the MsSiteRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsSite]:
        """
        Convert a database row into a MsSite dataclass.
        """
        if not row:
            return None

        try:
            return MsSite(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                site_id=getattr(row, "SiteId", None),
                display_name=getattr(row, "DisplayName", None),
                web_url=getattr(row, "WebUrl", None),
                hostname=getattr(row, "Hostname", None),
            )
        except AttributeError as error:
            logger.error("Attribute error during ms site mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during ms site mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, site_id: str, display_name: str, web_url: str, hostname: str) -> MsSite:
        """
        Create a new MsSite.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsSite",
                    params={
                        "SiteId": site_id,
                        "DisplayName": display_name,
                        "WebUrl": web_url,
                        "Hostname": hostname,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create ms site did not return a row.")
                    raise map_database_error(Exception("create ms site failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create ms site: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[MsSite]:
        """
        Read all MsSites.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsSites",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all ms sites: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsSite]:
        """
        Read a MsSite by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsSiteByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read ms site by public ID: %s", error)
            raise map_database_error(error)

    def read_by_site_id(self, site_id: str) -> Optional[MsSite]:
        """
        Read a MsSite by MS Graph site ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsSiteBySiteId",
                    params={
                        "SiteId": site_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read ms site by site ID: %s", error)
            raise map_database_error(error)

    def update_by_public_id(
        self, *, public_id: str, site_id: str, display_name: str, web_url: str, hostname: str
    ) -> Optional[MsSite]:
        """
        Update a MsSite by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateMsSiteByPublicId",
                    params={
                        "PublicId": public_id,
                        "SiteId": site_id,
                        "DisplayName": display_name,
                        "WebUrl": web_url,
                        "Hostname": hostname,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update ms site did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update ms site by public ID: %s", error)
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[MsSite]:
        """
        Delete a MsSite by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteMsSiteByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete ms site did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete ms site by public ID: %s", error)
            raise map_database_error(error)
