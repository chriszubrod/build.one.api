# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.item.business.model import QboItem
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboItemRepository:
    """
    Repository for QboItem persistence operations.
    """

    def __init__(self):
        """Initialize the QboItemRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboItem]:
        """
        Convert a database row into a QboItem dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return QboItem(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_id=getattr(row, "QboId", None),
                sync_token=getattr(row, "SyncToken", None),
                realm_id=getattr(row, "RealmId", None),
                name=getattr(row, "Name", None),
                description=getattr(row, "Description", None),
                active=getattr(row, "Active", None),
                type=getattr(row, "Type", None),
                parent_ref_value=getattr(row, "ParentRefValue", None),
                parent_ref_name=getattr(row, "ParentRefName", None),
                level=getattr(row, "Level", None),
                fully_qualified_name=getattr(row, "FullyQualifiedName", None),
                sku=getattr(row, "Sku", None),
                unit_price=Decimal(str(getattr(row, "UnitPrice"))) if getattr(row, "UnitPrice", None) is not None else None,
                purchase_cost=Decimal(str(getattr(row, "PurchaseCost"))) if getattr(row, "PurchaseCost", None) is not None else None,
                taxable=getattr(row, "Taxable", None),
                income_account_ref_value=getattr(row, "IncomeAccountRefValue", None),
                income_account_ref_name=getattr(row, "IncomeAccountRefName", None),
                expense_account_ref_value=getattr(row, "ExpenseAccountRefValue", None),
                expense_account_ref_name=getattr(row, "ExpenseAccountRefName", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo item mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo item mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        sync_token: Optional[str],
        realm_id: Optional[str],
        name: Optional[str],
        description: Optional[str],
        active: Optional[bool],
        type: Optional[str],
        parent_ref_value: Optional[str],
        parent_ref_name: Optional[str],
        level: Optional[int],
        fully_qualified_name: Optional[str],
        sku: Optional[str],
        unit_price: Optional[Decimal],
        purchase_cost: Optional[Decimal],
        taxable: Optional[bool],
        income_account_ref_value: Optional[str],
        income_account_ref_name: Optional[str],
        expense_account_ref_value: Optional[str],
        expense_account_ref_name: Optional[str],
    ) -> QboItem:
        """
        Create a new QboItem.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateQboItem",
                        params={
                            "QboId": qbo_id,
                            "SyncToken": sync_token,
                            "RealmId": realm_id,
                            "Name": name,
                            "Description": description,
                            "Active": active,
                            "Type": type,
                            "ParentRefValue": parent_ref_value,
                            "ParentRefName": parent_ref_name,
                            "Level": level,
                            "FullyQualifiedName": fully_qualified_name,
                            "Sku": sku,
                            "UnitPrice": float(unit_price) if unit_price is not None else None,
                            "PurchaseCost": float(purchase_cost) if purchase_cost is not None else None,
                            "Taxable": taxable,
                            "IncomeAccountRefValue": income_account_ref_value,
                            "IncomeAccountRefName": income_account_ref_name,
                            "ExpenseAccountRefValue": expense_account_ref_value,
                            "ExpenseAccountRefName": expense_account_ref_name,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Create qbo item did not return a row.")
                        raise map_database_error(Exception("create qbo item failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create qbo item: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[QboItem]:
        """
        Read all QboItems.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboItems",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read all qbo items: {error}")
            raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> List[QboItem]:
        """
        Read all QboItems by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboItemsByRealmId",
                        params={"RealmId": realm_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo items by realm ID: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboItem]:
        """
        Read a QboItem by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboItemById",
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
            logger.error(f"Error during read qbo item by ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboItem]:
        """
        Read a QboItem by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboItemByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo item by QBO ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id_and_realm_id(self, qbo_id: str, realm_id: str) -> Optional[QboItem]:
        """
        Read a QboItem by QBO ID and realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboItemByQboIdAndRealmId",
                        params={"QboId": qbo_id, "RealmId": realm_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read qbo item by QBO ID and realm ID: {error}")
            raise map_database_error(error)

    def update_by_qbo_id(
        self,
        qbo_id: str,
        row_version: bytes,
        sync_token: Optional[str],
        realm_id: Optional[str],
        name: Optional[str],
        description: Optional[str],
        active: Optional[bool],
        type: Optional[str],
        parent_ref_value: Optional[str],
        parent_ref_name: Optional[str],
        level: Optional[int],
        fully_qualified_name: Optional[str],
        sku: Optional[str],
        unit_price: Optional[Decimal],
        purchase_cost: Optional[Decimal],
        taxable: Optional[bool],
        income_account_ref_value: Optional[str],
        income_account_ref_name: Optional[str],
        expense_account_ref_value: Optional[str],
        expense_account_ref_name: Optional[str],
    ) -> Optional[QboItem]:
        """
        Update a QboItem by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboItemByQboId",
                        params={
                            "QboId": qbo_id,
                            "RowVersion": row_version,
                            "SyncToken": sync_token,
                            "RealmId": realm_id,
                            "Name": name,
                            "Description": description,
                            "Active": active,
                            "Type": type,
                            "ParentRefValue": parent_ref_value,
                            "ParentRefName": parent_ref_name,
                            "Level": level,
                            "FullyQualifiedName": fully_qualified_name,
                            "Sku": sku,
                            "UnitPrice": float(unit_price) if unit_price is not None else None,
                            "PurchaseCost": float(purchase_cost) if purchase_cost is not None else None,
                            "Taxable": taxable,
                            "IncomeAccountRefValue": income_account_ref_value,
                            "IncomeAccountRefName": income_account_ref_name,
                            "ExpenseAccountRefValue": expense_account_ref_value,
                            "ExpenseAccountRefName": expense_account_ref_name,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo item did not return a row.")
                        raise map_database_error(Exception("update qbo item by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update qbo item by QBO ID: {error}")
            raise map_database_error(error)

    def delete_by_qbo_id(self, qbo_id: str) -> Optional[QboItem]:
        """
        Delete a QboItem by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteQboItemByQboId",
                        params={"QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Delete qbo item did not return a row.")
                        raise map_database_error(Exception("delete qbo item by QBO ID failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete qbo item by QBO ID: {error}")
            raise map_database_error(error)

