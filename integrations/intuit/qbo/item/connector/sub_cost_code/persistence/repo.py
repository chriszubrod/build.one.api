# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.item.connector.sub_cost_code.business.model import ItemSubCostCode
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ItemSubCostCodeRepository:
    """
    Repository for ItemSubCostCode persistence operations.
    """

    def __init__(self):
        """Initialize the ItemSubCostCodeRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ItemSubCostCode]:
        """
        Convert a database row into an ItemSubCostCode dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return ItemSubCostCode(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                sub_cost_code_id=getattr(row, "SubCostCodeId", None),
                qbo_item_id=getattr(row, "QboItemId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during item sub cost code mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during item sub cost code mapping: {error}")
            raise map_database_error(error)

    def create(self, *, sub_cost_code_id: int, qbo_item_id: int) -> ItemSubCostCode:
        """
        Create a new ItemSubCostCode mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateItemSubCostCode",
                        params={
                            "SubCostCodeId": sub_cost_code_id,
                            "QboItemId": qbo_item_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateItemSubCostCode did not return a row.")
                        raise map_database_error(Exception("CreateItemSubCostCode failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create item sub cost code: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ItemSubCostCode]:
        """
        Read an ItemSubCostCode mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadItemSubCostCodeById",
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
            logger.error(f"Error during read item sub cost code by ID: {error}")
            raise map_database_error(error)

    def read_by_sub_cost_code_id(self, sub_cost_code_id: int) -> Optional[ItemSubCostCode]:
        """
        Read an ItemSubCostCode mapping record by SubCostCode ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadItemSubCostCodeBySubCostCodeId",
                        params={"SubCostCodeId": sub_cost_code_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read item sub cost code by sub cost code ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_item_id(self, qbo_item_id: int) -> Optional[ItemSubCostCode]:
        """
        Read an ItemSubCostCode mapping record by QboItem ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadItemSubCostCodeByQboItemId",
                        params={"QboItemId": qbo_item_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read item sub cost code by QBO item ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, item_sub_cost_code: ItemSubCostCode) -> Optional[ItemSubCostCode]:
        """
        Update an ItemSubCostCode mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateItemSubCostCodeById",
                        params={
                            "Id": item_sub_cost_code.id,
                            "RowVersion": item_sub_cost_code.row_version_bytes,
                            "SubCostCodeId": item_sub_cost_code.sub_cost_code_id,
                            "QboItemId": item_sub_cost_code.qbo_item_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("UpdateItemSubCostCodeById did not return a row.")
                        raise map_database_error(Exception("UpdateItemSubCostCodeById failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update item sub cost code by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ItemSubCostCode]:
        """
        Delete an ItemSubCostCode mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteItemSubCostCodeById",
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
            logger.error(f"Error during delete item sub cost code by ID: {error}")
            raise map_database_error(error)

