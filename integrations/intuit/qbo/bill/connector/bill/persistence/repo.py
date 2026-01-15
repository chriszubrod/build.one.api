# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.bill.connector.bill.business.model import BillBill
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BillBillRepository:
    """
    Repository for BillBill persistence operations.
    """

    def __init__(self):
        """Initialize the BillBillRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BillBill]:
        """
        Convert a database row into a BillBill dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return BillBill(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                bill_id=getattr(row, "BillId", None),
                qbo_bill_id=getattr(row, "QboBillId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during bill bill mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during bill bill mapping: {error}")
            raise map_database_error(error)

    def create(self, *, bill_id: int, qbo_bill_id: int) -> BillBill:
        """
        Create a new BillBill mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBillBill",
                        params={
                            "BillId": bill_id,
                            "QboBillId": qbo_bill_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateBillBill did not return a row.")
                        raise map_database_error(Exception("CreateBillBill failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create bill bill: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BillBill]:
        """
        Read a BillBill mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillBillById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read bill bill by ID: {error}")
            raise map_database_error(error)

    def read_by_bill_id(self, bill_id: int) -> Optional[BillBill]:
        """
        Read a BillBill mapping record by Bill ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillBillByBillId",
                        params={"BillId": bill_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read bill bill by bill ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_bill_id(self, qbo_bill_id: int) -> Optional[BillBill]:
        """
        Read a BillBill mapping record by QboBill ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillBillByQboBillId",
                        params={"QboBillId": qbo_bill_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read bill bill by QBO bill ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[BillBill]:
        """
        Delete a BillBill mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteBillBillById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete bill bill by ID: {error}")
            raise map_database_error(error)
