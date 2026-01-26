# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.mail.message.connector.bill.business.model import MsMessageBill
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class MsMessageBillRepository:
    """
    Repository for MsMessageBill persistence operations.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsMessageBill]:
        if not row:
            return None

        try:
            return MsMessageBill(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=str(getattr(row, "CreatedDatetime", None)) if getattr(row, "CreatedDatetime", None) else None,
                modified_datetime=str(getattr(row, "ModifiedDatetime", None)) if getattr(row, "ModifiedDatetime", None) else None,
                ms_message_id=getattr(row, "MsMessageId", None),
                bill_id=getattr(row, "BillId", None),
                notes=getattr(row, "Notes", None),
            )
        except Exception as error:
            logger.error("Error during MsMessageBill mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, ms_message_id: int, bill_id: int, notes: Optional[str] = None) -> MsMessageBill:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsMessageBill",
                    params={"MsMessageId": ms_message_id, "BillId": bill_id, "Notes": notes},
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("create MsMessageBill failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create MsMessageBill: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[MsMessageBill]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageBills", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all MsMessageBills: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsMessageBill]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageBillByPublicId", params={"PublicId": public_id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read MsMessageBill by public ID: %s", error)
            raise map_database_error(error)

    def read_by_ms_message_id(self, ms_message_id: int) -> list[MsMessageBill]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageBillsByMsMessageId", params={"MsMessageId": ms_message_id})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessageBills by message ID: %s", error)
            raise map_database_error(error)

    def read_by_bill_id(self, bill_id: int) -> list[MsMessageBill]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageBillsByBillId", params={"BillId": bill_id})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessageBills by bill ID: %s", error)
            raise map_database_error(error)

    def update_by_public_id(self, *, public_id: str, notes: Optional[str] = None) -> Optional[MsMessageBill]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="UpdateMsMessageBillByPublicId", params={"PublicId": public_id, "Notes": notes})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update MsMessageBill: %s", error)
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[MsMessageBill]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="DeleteMsMessageBillByPublicId", params={"PublicId": public_id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete MsMessageBill: %s", error)
            raise map_database_error(error)
