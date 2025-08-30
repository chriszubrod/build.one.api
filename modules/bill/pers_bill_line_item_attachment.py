"""
Module for attachment persistence.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from shared.database import get_db_connection
from shared.response import PersistenceResponse

@dataclass
class BillLineItemAttachment:
    """Data class to represent an attachment"""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    size: Optional[int] = None
    type: Optional[str] = None
    content: Optional[str] = None
    bill_line_item_id: Optional[int] = None


    @classmethod
    def from_db_row(cls, row) -> Optional['BillLineItemAttachment']:
        """Creates an BillLineItemAttachment instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            size=getattr(row, 'Size', None),
            type=getattr(row, 'Type', None),
            content=getattr(row, 'Content', None),
            bill_line_item_id=getattr(row, 'BillLineItemId', None),
        )


def create_bill_line_item_attachment(bill_line_item_attachment: BillLineItemAttachment) -> PersistenceResponse:
    """
    Creates a new bill line item attachment record in the database.

    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateBillLineItemAttachment(?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    bill_line_item_attachment.created_datetime,
                    bill_line_item_attachment.modified_datetime,
                    bill_line_item_attachment.name,
                    bill_line_item_attachment.size,
                    bill_line_item_attachment.type,
                    bill_line_item_attachment.content,
                    bill_line_item_attachment.bill_line_item_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Bill line item attachment created successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Bill line item attachment creation failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error in create bill line item attachment: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def get_bill_line_item_attachments() -> PersistenceResponse:
    """
    Retrieves all bill line item attachments.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadBillLineItemAttachments()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[BillLineItemAttachment.from_db_row(row) for row in rows],
                        message="Bill line item attachments retrieved successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=[],
                        message="No bill line item attachments found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in get bill line item attachments: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_bill_line_item_attachment_by_bill_line_item_id(bill_line_item_id: int) -> PersistenceResponse:
    """
    Retrieves all bill line item attachments for a given bill line item ID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadBillLineItemAttachmentByBillLineItemId(?)}"
                rows = cursor.execute(
                    sql,
                    bill_line_item_id
                ).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[BillLineItemAttachment.from_db_row(row) for row in rows],
                        message="Bill line item attachments retrieved successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No bill line item attachments found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in get bill line item attachments: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_bill_line_item_attachment(bill_line_item_attachment: BillLineItemAttachment) -> PersistenceResponse:
    """
    Updates a bill line item attachment record in the database.

    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateBillLineItemAttachment(?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    bill_line_item_attachment.id,
                    bill_line_item_attachment.name,
                    bill_line_item_attachment.size,
                    bill_line_item_attachment.type,
                    bill_line_item_attachment.content
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Bill line item attachment updated successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Bill line item attachment update failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error in update bill line item attachment: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
