# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.item.connector.cost_code.business.model import ItemCostCode
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ItemCostCodeRepository:
    """
    Repository for ItemCostCode persistence operations.
    """

    def __init__(self):
        """Initialize the ItemCostCodeRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ItemCostCode]:
        """
        Convert a database row into an ItemCostCode dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return ItemCostCode(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                cost_code_id=getattr(row, "CostCodeId", None),
                qbo_item_id=getattr(row, "QboItemId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during item cost code mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during item cost code mapping: {error}")
            raise map_database_error(error)

    def create(self, *, cost_code_id: int, qbo_item_id: int) -> ItemCostCode:
        """
        Create a new ItemCostCode mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateItemCostCode",
                        params={
                            "CostCodeId": cost_code_id,
                            "QboItemId": qbo_item_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateItemCostCode did not return a row.")
                        raise map_database_error(Exception("CreateItemCostCode failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create item cost code: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ItemCostCode]:
        """
        Read an ItemCostCode mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadItemCostCodeById",
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
            logger.error(f"Error during read item cost code by ID: {error}")
            raise map_database_error(error)

    def read_by_cost_code_id(self, cost_code_id: int) -> Optional[ItemCostCode]:
        """
        Read an ItemCostCode mapping record by CostCode ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadItemCostCodeByCostCodeId",
                        params={"CostCodeId": cost_code_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read item cost code by cost code ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_item_id(self, qbo_item_id: int) -> Optional[ItemCostCode]:
        """
        Read an ItemCostCode mapping record by QboItem ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadItemCostCodeByQboItemId",
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
            logger.error(f"Error during read item cost code by QBO item ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, item_cost_code: ItemCostCode) -> Optional[ItemCostCode]:
        """
        Update an ItemCostCode mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateItemCostCodeById",
                        params={
                            "Id": item_cost_code.id,
                            "RowVersion": item_cost_code.row_version_bytes,
                            "CostCodeId": item_cost_code.cost_code_id,
                            "QboItemId": item_cost_code.qbo_item_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("UpdateItemCostCodeById did not return a row.")
                        raise map_database_error(Exception("UpdateItemCostCodeById failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update item cost code by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ItemCostCode]:
        """
        Delete an ItemCostCode mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteItemCostCodeById",
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
            logger.error(f"Error during delete item cost code by ID: {error}")
            raise map_database_error(error)

