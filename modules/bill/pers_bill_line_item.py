"""
Module for entry line item.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from persistence import pers_database
from persistence.pers_response import PersistenceResponse


@dataclass
class BillLineItem:
    """Represents an entry in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    description: Optional[str] = None
    units: Optional[int] = None
    rate: Optional[float] = None
    amount: Optional[float] = None
    is_billable: Optional[bool] = None
    is_billed: Optional[bool] = None
    bill_id: Optional[int] = None
    sub_cost_code_id: Optional[int] = None
    project_id: Optional[int] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['BillLineItem']:
        """Creates an BillLineItem instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            description=getattr(row, 'Description', None),
            units=getattr(row, 'Units', None),
            rate=getattr(row, 'Rate', None),
            amount=getattr(row, 'Amount', None),
            is_billable=getattr(row, 'IsBillable', None),
            is_billed=getattr(row, 'IsBilled', None),
            bill_id=getattr(row, 'BillId', None),
            sub_cost_code_id=getattr(row, 'SubCostCodeId', None),
            project_id=getattr(row, 'ProjectId', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_bill_line_item(bill_line_item: BillLineItem) -> PersistenceResponse:
    """Creates a new bill line item in the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateBillLineItem(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                row_count = cursor.execute(
                    sql,
                    bill_line_item.created_datetime,
                    bill_line_item.modified_datetime,
                    bill_line_item.description,
                    bill_line_item.units,
                    bill_line_item.rate,
                    bill_line_item.amount,
                    bill_line_item.is_billable,
                    bill_line_item.is_billed,
                    bill_line_item.bill_id,
                    bill_line_item.sub_cost_code_id,
                    bill_line_item.project_id,
                    bill_line_item.transaction_id
                ).rowcount
                cnxn.commit()
                if row_count > 0:
                    return PersistenceResponse(
                        data=row_count,
                        message="Bill Line Item has been successfully created.",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Bill Line Item has NOT been successfully created.",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create Bill Line Item: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_bill_line_item_by_id(bill_line_item_id: int) -> PersistenceResponse:
    """Retrieves a bill line item from the database by ID."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadBuildoneBillLineItemById(?)}"
                row = cursor.execute(sql, bill_line_item_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=BillLineItem.from_db_row(row),
                        message="Bill line item by id found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Bill line item by id not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read bill line item by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_bill_line_item_by_bill_id(bill_id: int) -> PersistenceResponse:
    """Retrieves all bill line items for a specific bill."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadBillLineItemByBillId(?)}"
                rows = cursor.execute(sql, bill_id).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[BillLineItem.from_db_row(row) for row in rows],
                        message="Bill line item by bill id found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Bill line item by bill id not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read bill line item by bill id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
