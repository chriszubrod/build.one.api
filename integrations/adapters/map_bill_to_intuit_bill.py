"""
This module contains the persistence layer for the Map Bill Intuit Bill.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pyodbc

from integrations.adapters import register_adapter
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@register_adapter
@dataclass
class MapBillToIntuitBill:
    """Represents a Map Bill Intuit Bill in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    bill_id: Optional[int] = None
    intuit_bill_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'MapBillToIntuitBill':
        """Creates a MapBillToIntuitBill object from a database row."""
        if not row:
            return None

        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            bill_id=getattr(row, 'BillId'),
            intuit_bill_id=getattr(row, 'IntuitBillId'),
        )


def create_map_bill_to_intuit_bill(
        bill_id: int,
        intuit_bill_id: int
    ) -> PersistenceResponse:
    """
    Creates a Map Bill Intuit Bill in the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMapBillIntuitBill (?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    bill_id,
                    intuit_bill_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Bill Intuit Bill created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Map Bill Intuit Bill not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create Map Bill Intuit Bill: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_intuit_bills() -> PersistenceResponse:
    """
    Retrieves all Map Bill Intuit Bills from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapBillIntuitBills}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MapBillToIntuitBill.from_db_row(row) for row in rows],
                        message="Map Bill Intuit Bills found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Bill Intuit Bills found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Bill Intuit Bills: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_bill_to_intuit_bill_by_bill_id(bill_id: int) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapBillIntuitBillByBillId (?)}"
                row = cursor.execute(sql, int(bill_id)).fetchone()

                if row:
                    return PersistenceResponse(
                        data=MapBillToIntuitBill.from_db_row(row),
                        message="Map Bill Intuit Bill found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Bill Intuit Bill found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Bill Intuit Bill: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_bill_to_intuit_bill_by_intuit_bill_id(intuit_bill_id: int) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapBillIntuitBillByIntuitBillId (?)}"
                rows = cursor.execute(sql, int(intuit_bill_id)).fetchone()

                if rows:
                    return PersistenceResponse(
                        data=[MapBillToIntuitBill.from_db_row(row) for row in rows],
                        message="Map Bill Intuit Bills found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Bill Intuit Bill found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Bill Intuit Bill: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_map_bill_to_intuit_bill(map_bill_to_intuit_bill):
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMapBillIntuitBillById (?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    map_bill_to_intuit_bill.id,
                    map_bill_to_intuit_bill.bill_id,
                    map_bill_to_intuit_bill.intuit_bill_id
                ).rowcount
                cnxn.commit()

                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Bill Intuit Bill updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Map Bill Intuit Bill not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update Map Bill Intuit Bill: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


