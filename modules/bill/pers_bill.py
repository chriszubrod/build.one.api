"""
Module for entry.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


# third party imports
import pyodbc


# local imports
from persistence import pers_database
from persistence.pers_response import PersistenceResponse
from modules.bill import (
    bus_bill_line_item_attachment,
    pers_bill
)


@dataclass
class Bill:
    """Represents a bill in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    number: Optional[str] = None
    date: Optional[datetime] = None
    amount: Optional[float] = None
    vendor_id: Optional[int] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['Bill']:
        """Creates a Bill instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            number=getattr(row, 'Number', None),
            date=getattr(row, 'Date', None),
            amount=getattr(row, 'Amount', None),
            vendor_id=getattr(row, 'VendorId', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_bill_with_line_items_and_attachments(
        bill: Bill,
        line_items: list,
        attachments: list
) -> PersistenceResponse:
    """Creates a new bill with line items and attachments in the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                # Convert line items and attachments to pyodbc.Row compatible format
                '''
                line_items_params = [(
                    item.created_datetime,
                    item.modified_datetime,
                    item.description,
                    int(item.units),
                    item.rate,
                    item.amount,
                    item.is_billable,
                    item.is_billed,
                    item.sub_cost_code_id,
                    item.project_id
                ) for item in line_items]

                attachments_params = [(
                    att.created_datetime,
                    att.modified_datetime,
                    att.name,
                    att.text,
                    att.number_of_pages,
                    att.file_path,
                    att.file_size,
                    att.file_type
                ) for att in attachments]
                '''
                # Debug prints
                #print("Bill data:", bill)
                #print("Line items:", line_items_params)
                #print("Attachments:", attachments_params)

                sql = "{CALL CreateBillWithLineItemsAndAttachments(?, ?, ?, ?, ?, ?, ?, ?)}"
                row_count = cursor.execute(
                    sql,
                    bill.created_datetime,
                    bill.modified_datetime,
                    bill.number,
                    bill.date,
                    bill.amount,
                    bill.vendor_id,
                    line_items,
                    attachments
                ).rowcount
                cnxn.commit()
                if row_count > 0:
                    return PersistenceResponse(
                        data=row_count,
                        message=(
                            "Bill with line items and attachments has been successfully created."
                        ),
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message=(
                            "Bill with line items and attachments has NOT been created."
                        ),
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create bill with line items and attachments: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def create_bill(bill: Bill) -> PersistenceResponse:
    """Creates a new bill in the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateBill(?, ?, ?, ?, ?, ?, ?, ?)}"
                row_count = cursor.execute(
                    sql,
                    bill.created_datetime,
                    bill.modified_datetime,
                    bill.number,
                    bill.date,
                    bill.amount,
                    bill.vendor_id,
                    bill.attachment_id,
                    bill.transaction_id
                ).rowcount
                cnxn.commit()
                if row_count > 0:
                    return PersistenceResponse(
                        data=row_count,
                        message="Bill has been successfully created.",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Bill has NOT been successfully created.",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create bill: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_bills() -> PersistenceResponse:
    """Retrieves all bills from the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadBills()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[Bill.from_db_row(row) for row in rows],
                        message="Bills found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Bills not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read bills: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_bill_by_id(bill_id: int) -> PersistenceResponse:
    """Retrieves a bill from the database by ID."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadBillById(?)}"
                row = cursor.execute(sql, bill_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Bill.from_db_row(row),
                        message="Bill by id found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Bill by id not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read bill by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_bill_by_guid(bill_guid: str) -> PersistenceResponse:
    """Retrieves a bill from the database by GUID."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadBillByGuid(?)}"
                row = cursor.execute(sql, bill_guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Bill.from_db_row(row),
                        message="Bill by guid found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Bill by guid not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read bill by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_bills_by_vendor(vendor_id: int) -> PersistenceResponse:
    """Retrieves all bills for a specific vendor."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadBillsByVendor(?)}"
                rows = cursor.execute(sql, vendor_id).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[Bill.from_db_row(row) for row in rows],
                        message="Bills by vendor found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Bills by vendor not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read bills by vendor: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
