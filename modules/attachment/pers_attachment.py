"""
Module for attachment persistence.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict

# third party imports
import pyodbc

# local imports
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class Attachment:
    """Represents an attachment in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    size: Optional[int] = None
    type: Optional[str] = None
    storage_account: Optional[str] = None
    container_name: Optional[str] = None
    blob_name: Optional[str] = None
    e_tag: Optional[str] = None
    sha_256_hash: Optional[str] = None
    tags: Optional[Dict[str, str]] = None
    metadata: Optional[Dict[str, str]] = None


    @classmethod
    def from_db_row(cls, row) -> Optional['Attachment']:
        """Creates an Attachment instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            size=getattr(row, 'Size', None),
            type=getattr(row, 'Type', None),
            storage_account=getattr(row, 'StorageAccount', None),
            container_name=getattr(row, 'ContainerName', None),
            blob_name=getattr(row, 'BlobName', None),
            e_tag=getattr(row, 'ETag', None),
            sha_256_hash=getattr(row, 'Sha256Hash', None),
            tags=getattr(row, 'Tags', None),
            metadata=getattr(row, 'Metadata', None)
        )


def create_attachment(attachment: Attachment) -> PersistenceResponse:
    """
    Creates a new attachment record in the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateAttachment(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    attachment.name,
                    attachment.size,
                    attachment.type,
                    attachment.storage_account,
                    attachment.container_name,
                    attachment.blob_name,
                    attachment.e_tag,
                    attachment.sha_256_hash,
                    attachment.tags,
                    attachment.metadata
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


def read_attachments() -> PersistenceResponse:
    """
    Retrieves all attachments from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadAttachments()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[Attachment.from_db_row(row) for row in rows],
                        message="Attachments retrieved successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No attachments found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            print(f"Error in read attachments: {str(e)}")
            return PersistenceResponse(
                data=None,
                message=f"Error in read attachments: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_attachment_by_id(attachment_id: int) -> PersistenceResponse:
    """
    Retrieves an attachment from the database by ID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadAttachmentById(?)}"
                row = cursor.execute(sql, attachment_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Attachment.from_db_row(row),
                        message="Attachment found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Attachment not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in read attachment by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_attachment_by_guid(attachment_guid: str) -> PersistenceResponse:
    """
    Retrieves an attachment from the database by GUID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadAttachmentByGuid(?)}"
                row = cursor.execute(sql, attachment_guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Attachment.from_db_row(row),
                        message="Attachment found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Attachment not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in read attachment by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_attachment_by_id(attachment: Attachment) -> PersistenceResponse:
    """
    Updates an attachment in the database by ID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateAttachmentById(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    attachment.id,
                    attachment.name,
                    attachment.size,
                    attachment.type,
                    attachment.storage_account,
                    attachment.container_name,
                    attachment.blob_name,
                    attachment.e_tag,
                    attachment.sha_256_hash,
                    attachment.tags,
                    attachment.metadata
                ).rowcount
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Attachment updated successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Attachment update failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error in update attachment by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_attachment_by_id(attachment_id: int) -> PersistenceResponse:
    """
    Deletes an attachment from the database by ID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteAttachmentById(?)}"
                rowcount = cursor.execute(sql, attachment_id).rowcount
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Attachment deleted successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Attachment deletion failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in delete attachment by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
