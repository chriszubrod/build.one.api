# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.bill.connector.bill_line_item.business.model import BillLineItemBillLine
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BillLineItemBillLineRepository:
    """
    Repository for BillLineItemBillLine persistence operations.
    """

    def __init__(self):
        """Initialize the BillLineItemBillLineRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BillLineItemBillLine]:
        """
        Convert a database row into a BillLineItemBillLine dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return BillLineItemBillLine(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                bill_line_item_id=getattr(row, "BillLineItemId", None),
                qbo_bill_line_id=getattr(row, "QboBillLineId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during bill line item bill line mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during bill line item bill line mapping: {error}")
            raise map_database_error(error)

    def create(self, *, bill_line_item_id: int, qbo_bill_line_id: int) -> BillLineItemBillLine:
        """
        Create a new BillLineItemBillLine mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBillLineItemBillLine",
                        params={
                            "BillLineItemId": bill_line_item_id,
                            "QboBillLineId": qbo_bill_line_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateBillLineItemBillLine did not return a row.")
                        raise map_database_error(Exception("CreateBillLineItemBillLine failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create bill line item bill line: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BillLineItemBillLine]:
        """
        Read a BillLineItemBillLine mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillLineItemBillLineById",
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
            logger.error(f"Error during read bill line item bill line by ID: {error}")
            raise map_database_error(error)

    def read_by_bill_line_item_id(self, bill_line_item_id: int) -> Optional[BillLineItemBillLine]:
        """
        Read a BillLineItemBillLine mapping record by BillLineItem ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillLineItemBillLineByBillLineItemId",
                        params={"BillLineItemId": bill_line_item_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read bill line item bill line by bill line item ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_bill_line_id(self, qbo_bill_line_id: int) -> Optional[BillLineItemBillLine]:
        """
        Read a BillLineItemBillLine mapping record by QboBillLine ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillLineItemBillLineByQboBillLineId",
                        params={"QboBillLineId": qbo_bill_line_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read bill line item bill line by QBO bill line ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[BillLineItemBillLine]:
        """
        Delete a BillLineItemBillLine mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteBillLineItemBillLineById",
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
            logger.error(f"Error during delete bill line item bill line by ID: {error}")
            raise map_database_error(error)
