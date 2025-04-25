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
from persistence import pers_database
from persistence.pers_response import PersistenceResponse

@dataclass
class Attachment:
    """Data class to represent an attachment"""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    text: Optional[str] = None
    number_of_pages: Optional[int] = None
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    file_path: Optional[str] = None
    transaction_id: Optional[int] = None


    @classmethod
    def from_db_row(cls, row) -> Optional['Attachment']:
        """Creates an Attachment instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            text=getattr(row, 'Text', None),
            number_of_pages=getattr(row, 'NumberOfPages', None),
            file_path=getattr(row, 'FilePath', None),
            file_size=getattr(row, 'FileSize', None),
            file_type=getattr(row, 'FileType', None),
            transaction_id=getattr(row, 'TransactionId', None),
        )


def create_attachment(attachment: Attachment) -> PersistenceResponse:
    """
    Creates a new attachment record in the database.

    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateAttachment(?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    attachment.created_datetime,
                    attachment.modified_datetime,
                    attachment.name,
                    attachment.text,
                    attachment.number_of_pages,
                    attachment.file_size,
                    attachment.file_type,
                    attachment.file_path
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Attachment created successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Attachment creation failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error in create attachment: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
