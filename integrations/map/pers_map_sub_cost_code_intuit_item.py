"""
This module contains the persistence layer for the Map Bill Intuit Bill.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

import persistence.pers_database as pers_database
from persistence.pers_response import DatabaseError, SuccessResponse, PersistenceResponse


@dataclass
class MapSubCostCodeIntuitItem:
    """Represents a Map Sub Cost Code Intuit Item in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    sub_cost_code_id: Optional[int] = None
    intuit_item_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'MapSubCostCodeIntuitItem':
        """Creates a MapSubCostCodeIntuitItem object from a database row."""
        if not row:
            return None

        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            sub_cost_code_id=getattr(row, 'SubCostCodeId'),
            intuit_item_id=getattr(row, 'IntuitItemId'),
        )


def create_map_sub_cost_code_intuit_item(
        sub_cost_code_id: int,
        intuit_item_id: int
    ) -> PersistenceResponse:
    """
    Creates a Map Sub Cost Code Intuit Item in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMapSubCostCodeIntuitItem (?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sub_cost_code_id,
                    intuit_item_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Sub Cost Code Intuit Item created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Map Sub Cost Code Intuit Item not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create Map Sub Cost Code Intuit Item: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_sub_cost_code_intuit_items() -> PersistenceResponse:
    """
    Retrieves all Map Sub Cost Code Intuit Items from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapSubCostCodeIntuitItems}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MapSubCostCodeIntuitItem.from_db_row(row) for row in rows],
                        message="Map Sub Cost Code Intuit Items found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Sub Cost Code Intuit Items found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Sub Cost Code Intuit Items: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_sub_cost_code_intuit_item_by_sub_cost_code_id(sub_cost_code_id: int) -> PersistenceResponse:
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapSubCostCodeIntuitItemBySubCostCodeId (?)}"
                row = cursor.execute(sql, int(sub_cost_code_id)).fetchone()

                if row:
                    return PersistenceResponse(
                        data=MapSubCostCodeIntuitItem.from_db_row(row),
                        message="Map Sub Cost Code Intuit Item found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Sub Cost Code Intuit Item found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Sub Cost Code Intuit Item: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_sub_cost_code_intuit_item_by_intuit_item_id(intuit_item_id: int) -> PersistenceResponse:
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapSubCostCodeIntuitItemByIntuitItemId (?)}"
                rows = cursor.execute(sql, int(intuit_item_id)).fetchone()

                if rows:
                    return PersistenceResponse(
                        data=[MapSubCostCodeIntuitItem.from_db_row(row) for row in rows],
                        message="Map Sub Cost Code Intuit Items found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Sub Cost Code Intuit Item found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Sub Cost Code Intuit Item: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_map_sub_cost_code_intuit_item(map_sub_cost_code_intuit_item):
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMapSubCostCodeIntuitItemById (?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    map_sub_cost_code_intuit_item.id,
                    map_sub_cost_code_intuit_item.sub_cost_code_id,
                    map_sub_cost_code_intuit_item.intuit_item_id
                ).rowcount
                cnxn.commit()

                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Sub Cost Code Intuit Item updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Map Sub Cost Code Intuit Item not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update Map Sub Cost Code Intuit Item: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


