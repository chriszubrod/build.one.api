# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from modules.attachment.business.model import Attachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class AttachmentRepository:
    """
    Repository for Attachment persistence operations.
    """

    def __init__(self):
        """Initialize the AttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Attachment]:
        """
        Convert a database row into an Attachment dataclass.
        """
        if not row:
            return None

        try:
            return Attachment(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                filename=row.Filename,
                original_filename=row.OriginalFilename,
                file_extension=row.FileExtension,
                content_type=row.ContentType,
                file_size=row.FileSize,
                file_hash=row.FileHash,
                blob_url=row.BlobUrl,
                description=row.Description,
                category=row.Category,
                tags=row.Tags,
                is_archived=row.IsArchived,
                status=row.Status,
                download_count=row.DownloadCount,
                last_downloaded_datetime=row.LastDownloadedDatetime,
                expiration_date=row.ExpirationDate,
                storage_tier=row.StorageTier,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during attachment mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        filename: Optional[str],
        original_filename: Optional[str],
        file_extension: Optional[str],
        content_type: Optional[str],
        file_size: Optional[int],
        file_hash: Optional[str],
        blob_url: Optional[str],
        description: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[str] = None,
        is_archived: bool = False,
        status: Optional[str] = None,
        expiration_date: Optional[str] = None,
        storage_tier: str = "Hot",
    ) -> Attachment:
        """
        Create a new attachment.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "Filename": filename,
                        "OriginalFilename": original_filename,
                        "FileExtension": file_extension,
                        "ContentType": content_type,
                        "FileSize": file_size,
                        "FileHash": file_hash,
                        "BlobUrl": blob_url,
                        "Description": description,
                        "Category": category,
                        "Tags": tags,
                        "IsArchived": 1 if is_archived else 0,
                        "Status": status,
                        "ExpirationDate": expiration_date,
                        "StorageTier": storage_tier,
                    }
                    call_procedure(
                        cursor=cursor,
                        name="CreateAttachment",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateAttachment did not return a row.")
                        raise map_database_error(Exception("CreateAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create attachment: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Attachment]:
        """
        Read all attachments.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachments",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read all attachments: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Attachment]:
        """
        Read an attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Attachment]:
        """
        Read an attachment by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachmentByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read attachment by public ID: {error}")
            raise map_database_error(error)

    def read_by_category(self, category: str) -> list[Attachment]:
        """
        Read attachments by category.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachmentByCategory",
                        params={"Category": category},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read attachments by category: {error}")
            raise map_database_error(error)

    def read_by_hash(self, file_hash: str) -> Optional[Attachment]:
        """
        Read an attachment by file hash.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachmentByHash",
                        params={"FileHash": file_hash},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read attachment by hash: {error}")
            raise map_database_error(error)

    def update_by_id(self, attachment: Attachment) -> Optional[Attachment]:
        """
        Update an attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "Id": attachment.id,
                        "RowVersion": attachment.row_version_bytes,
                        "Filename": attachment.filename,
                        "OriginalFilename": attachment.original_filename,
                        "FileExtension": attachment.file_extension,
                        "ContentType": attachment.content_type,
                        "FileSize": attachment.file_size,
                        "FileHash": attachment.file_hash,
                        "BlobUrl": attachment.blob_url,
                        "Description": attachment.description,
                        "Category": attachment.category,
                        "Tags": attachment.tags,
                        "IsArchived": 1 if attachment.is_archived else 0,
                        "Status": attachment.status,
                        "ExpirationDate": attachment.expiration_date,
                        "StorageTier": attachment.storage_tier,
                    }
                    call_procedure(
                        cursor=cursor,
                        name="UpdateAttachmentById",
                        params=params,
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during update attachment by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Attachment]:
        """
        Delete an attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during delete attachment by ID: {error}")
            raise map_database_error(error)

    def increment_download_count(self, id: int) -> Optional[Attachment]:
        """
        Increment download count and update last downloaded datetime.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="IncrementDownloadCount",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during increment download count: {error}")
            raise map_database_error(error)

