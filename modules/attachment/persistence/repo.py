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
            # Handle extraction fields with backwards compatibility
            extraction_status = getattr(row, 'ExtractionStatus', None)
            extracted_text_blob_url = getattr(row, 'ExtractedTextBlobUrl', None)
            extraction_error = getattr(row, 'ExtractionError', None)
            extracted_datetime = getattr(row, 'ExtractedDatetime', None)
            # Handle categorization fields with backwards compatibility
            ai_category = getattr(row, 'AICategory', None)
            ai_category_confidence = getattr(row, 'AICategoryConfidence', None)
            ai_category_status = getattr(row, 'AICategoryStatus', None)
            ai_category_reasoning = getattr(row, 'AICategoryReasoning', None)
            ai_extracted_fields = getattr(row, 'AIExtractedFields', None)
            categorized_datetime = getattr(row, 'CategorizedDatetime', None)

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
                extraction_status=extraction_status,
                extracted_text_blob_url=extracted_text_blob_url,
                extraction_error=extraction_error,
                extracted_datetime=extracted_datetime,
                ai_category=ai_category,
                ai_category_confidence=float(ai_category_confidence) if ai_category_confidence else None,
                ai_category_status=ai_category_status,
                ai_category_reasoning=ai_category_reasoning,
                ai_extracted_fields=ai_extracted_fields,
                categorized_datetime=categorized_datetime,
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

    def update_extraction(
        self,
        id: int,
        extraction_status: str,
        extracted_text_blob_url: Optional[str] = None,
        extraction_error: Optional[str] = None,
    ) -> Optional[Attachment]:
        """
        Update extraction status and results for an attachment.

        Args:
            id: Attachment ID
            extraction_status: 'pending', 'processing', 'completed', or 'failed'
            extracted_text_blob_url: URL to JSON extraction results in blob storage
            extraction_error: Error message (for failed status)

        Returns:
            Updated Attachment or None if not found
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "Id": id,
                        "ExtractionStatus": extraction_status,
                        "ExtractedTextBlobUrl": extracted_text_blob_url,
                        "ExtractionError": extraction_error,
                    }
                    call_procedure(
                        cursor=cursor,
                        name="UpdateAttachmentExtraction",
                        params=params,
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during update extraction: {error}")
            raise map_database_error(error)

    def read_pending_extraction(self) -> list[Attachment]:
        """
        Read all attachments pending extraction.

        Returns:
            List of Attachments with extraction_status = 'pending' or NULL
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachmentsPendingExtraction",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read pending extraction: {error}")
            raise map_database_error(error)

    def update_categorization(
        self,
        id: int,
        ai_category: str,
        ai_category_confidence: float,
        ai_category_status: str,
        ai_category_reasoning: Optional[str] = None,
        ai_extracted_fields: Optional[str] = None,
    ) -> Optional[Attachment]:
        """
        Update AI categorization for an attachment.

        Args:
            id: Attachment ID
            ai_category: The determined category
            ai_category_confidence: Confidence score (0.0-1.0)
            ai_category_status: 'auto_assigned', 'suggested', 'manual', etc.
            ai_category_reasoning: Explanation for the categorization
            ai_extracted_fields: JSON string of extracted fields

        Returns:
            Updated Attachment or None if not found
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "Id": id,
                        "AICategory": ai_category,
                        "AICategoryConfidence": ai_category_confidence,
                        "AICategoryStatus": ai_category_status,
                        "AICategoryReasoning": ai_category_reasoning,
                        "AIExtractedFields": ai_extracted_fields,
                    }
                    call_procedure(
                        cursor=cursor,
                        name="UpdateAttachmentCategorization",
                        params=params,
                    )
                    row = cursor.fetchone()
                    # Re-fetch to get full attachment data
                    if row:
                        return self.read_by_id(id)
                    return None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during update categorization: {error}")
            raise map_database_error(error)

    def read_pending_categorization(self, limit: int = 50) -> list[Attachment]:
        """
        Read attachments pending categorization.

        Returns:
            List of Attachments that are extracted but not yet categorized
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachmentsPendingCategorization",
                        params={"Limit": limit},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read pending categorization: {error}")
            raise map_database_error(error)

    def confirm_categorization(
        self,
        id: int,
        confirmed: bool,
        manual_category: Optional[str] = None,
    ) -> Optional[Attachment]:
        """
        Confirm or reject AI categorization.

        Args:
            id: Attachment ID
            confirmed: True to confirm AI suggestion, False to reject
            manual_category: If rejecting, the correct category to assign

        Returns:
            Updated Attachment or None if not found
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    params = {
                        "Id": id,
                        "Confirmed": 1 if confirmed else 0,
                        "ManualCategory": manual_category,
                    }
                    call_procedure(
                        cursor=cursor,
                        name="ConfirmAttachmentCategorization",
                        params=params,
                    )
                    row = cursor.fetchone()
                    if row:
                        return self.read_by_id(id)
                    return None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during confirm categorization: {error}")
            raise map_database_error(error)

