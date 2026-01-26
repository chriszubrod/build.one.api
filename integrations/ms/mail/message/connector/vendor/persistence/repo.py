# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.mail.message.connector.vendor.business.model import MsMessageVendor
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class MsMessageVendorRepository:
    """
    Repository for MsMessageVendor persistence operations.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsMessageVendor]:
        if not row:
            return None

        try:
            return MsMessageVendor(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=str(getattr(row, "CreatedDatetime", None)) if getattr(row, "CreatedDatetime", None) else None,
                modified_datetime=str(getattr(row, "ModifiedDatetime", None)) if getattr(row, "ModifiedDatetime", None) else None,
                ms_message_id=getattr(row, "MsMessageId", None),
                vendor_id=getattr(row, "VendorId", None),
                notes=getattr(row, "Notes", None),
            )
        except Exception as error:
            logger.error("Error during MsMessageVendor mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, ms_message_id: int, vendor_id: int, notes: Optional[str] = None) -> MsMessageVendor:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsMessageVendor",
                    params={"MsMessageId": ms_message_id, "VendorId": vendor_id, "Notes": notes},
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("create MsMessageVendor failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create MsMessageVendor: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[MsMessageVendor]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageVendors", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all MsMessageVendors: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsMessageVendor]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageVendorByPublicId", params={"PublicId": public_id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read MsMessageVendor by public ID: %s", error)
            raise map_database_error(error)

    def read_by_ms_message_id(self, ms_message_id: int) -> list[MsMessageVendor]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageVendorsByMsMessageId", params={"MsMessageId": ms_message_id})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessageVendors by message ID: %s", error)
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> list[MsMessageVendor]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageVendorsByVendorId", params={"VendorId": vendor_id})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessageVendors by vendor ID: %s", error)
            raise map_database_error(error)

    def update_by_public_id(self, *, public_id: str, notes: Optional[str] = None) -> Optional[MsMessageVendor]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="UpdateMsMessageVendorByPublicId", params={"PublicId": public_id, "Notes": notes})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update MsMessageVendor: %s", error)
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[MsMessageVendor]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="DeleteMsMessageVendorByPublicId", params={"PublicId": public_id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete MsMessageVendor: %s", error)
            raise map_database_error(error)
