from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

from shared.database import get_db_connection
from shared.response import PersistenceResponse

@dataclass
class IntuitItem:
    """Intuit Item data class."""
    item_guid: Optional[str] = None
    realm_id: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[int] = None
    is_sub_item: Optional[int] = None
    parent_ref_value: Optional[str] = None
    level: Optional[int] = None
    fully_qualified_name: Optional[str] = None
    item_id: Optional[str] = None
    sync_token: Optional[str] = None
    created_datetime: Optional[datetime] = None
    last_update_datetime: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row):
        """Create an IntuitItem object from a database row."""
        return cls(
            item_guid=getattr(row, 'ItemGUID'),
            realm_id=getattr(row, 'RealmId'),
            name=getattr(row, 'Name'),
            is_active=getattr(row, 'IsActive'),
            is_sub_item=getattr(row, 'IsSubItem'),
            parent_ref_value=getattr(row, 'ParentRefValue'),
            level=getattr(row, 'Level'),
            fully_qualified_name=getattr(row, 'FullyQualifiedName'),
            item_id=getattr(row, 'Id'),
            sync_token=getattr(row, 'SyncToken'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            last_update_datetime=getattr(row, 'LastUpdatedDatetime')
        )


def create_intuit_item(realm_id, intuit_item):
    """Create intuit item."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateIntuitItem(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                row_count = cursor.execute(
                    sql,
                    realm_id,
                    intuit_item.name,
                    intuit_item.is_active,
                    intuit_item.is_sub_item,
                    intuit_item.parent_ref_value,
                    intuit_item.level,
                    intuit_item.fully_qualified_name,
                    intuit_item.item_id,
                    intuit_item.sync_token,
                    intuit_item.created_datetime,
                    intuit_item.last_update_datetime
                ).rowcount
                if row_count == 1:
                    return SuccessResponse(
                        message="Intuit Item has been successfully created.",
                        data=row_count,
                        status_code=201
                    )
                else:
                    return BusinessResponse(
                        message="Intuit Item has NOT been successfully created.",
                        status_code=501
                    )
        except pyodbc.DatabaseError as err:
            raise DatabaseError(f"Failed to create Intuit Item: {str(err)}") from err


def read_intuit_item_by_id(item_id) -> PersistenceResponse:
    """Read intuit item by id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadIntuitItemById(?)}"
                row = cursor.execute(sql, item_id).fetchone()
                if row:
                    return PersistenceResponse( 
                        message="Intuit Item found",
                        data=IntuitItem.from_db_row(row),
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        message="Intuit Item not found",
                        data=None,
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except pyodbc.DatabaseError as err:
            return PersistenceResponse(
                message=f"Failed to read Intuit Item: {str(err)}",
                data=None,
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_intuit_item_by_realm_id_and_item_id(realm_id, intuit_item):
    """Update intuit item by realm id and item id."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateIntuitItemByRealmIdAndItemId(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                row_count = cursor.execute(
                    sql,
                    realm_id,
                    intuit_item.name,
                    intuit_item.is_active,
                    intuit_item.is_sub_item,
                    intuit_item.parent_ref_value,
                    intuit_item.level,
                    intuit_item.fully_qualified_name,
                    intuit_item.item_id,
                    intuit_item.sync_token,
                    intuit_item.created_datetime,
                    intuit_item.last_update_datetime
                ).rowcount
                if row_count == 1:
                    return SuccessResponse(
                        message="Intuit Item has been successfully updated.",
                        data=row_count,
                        status_code=201
                    )
                else:
                    return BusinessResponse(
                        message="Intuit Item has NOT been successfully updated.",
                        status_code=404
                    )
        except pyodbc.DatabaseError as err:
            raise DatabaseError(f"Failed to update Intuit Item: {str(err)}") from err
