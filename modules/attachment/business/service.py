# Python Standard Library Imports
import hashlib
import logging
import os
from typing import Optional

# Third-party Imports

# Local Imports
from modules.attachment.business.model import Attachment
from modules.attachment.persistence.repo import AttachmentRepository

logger = logging.getLogger(__name__)


class AttachmentService:
    """
    Service for Attachment entity business operations.
    """

    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

    def __init__(self, repo: Optional[AttachmentRepository] = None):
        """Initialize the AttachmentService."""
        self.repo = repo or AttachmentRepository()

    @staticmethod
    def calculate_hash(file_content: bytes) -> str:
        """
        Calculate SHA-256 hash of file content.
        """
        return hashlib.sha256(file_content).hexdigest()

    @staticmethod
    def extract_extension(filename: str) -> Optional[str]:
        """
        Extract file extension from filename.
        """
        if not filename:
            return None
        _, ext = os.path.splitext(filename)
        return ext.lstrip(".") if ext else None

    def validate_file_size(self, file_size: int) -> None:
        """
        Validate file size does not exceed maximum.
        """
        if file_size > self.MAX_FILE_SIZE:
            raise ValueError(f"File size exceeds maximum of {self.MAX_FILE_SIZE / (1024 * 1024):.0f} MB")

    def validate_content_type(self, content_type: str, allowed_types: Optional[list] = None) -> None:
        """
        Validate content type against allowed list (optional).
        """
        if allowed_types and content_type not in allowed_types:
            raise ValueError(f"Content type '{content_type}' is not allowed")

    def is_expired(self, attachment: Attachment) -> bool:
        """
        Check if attachment expiration date has passed.
        """
        if not attachment.expiration_date:
            return False
        from datetime import datetime
        try:
            exp_date = datetime.fromisoformat(attachment.expiration_date.replace("Z", "+00:00"))
            return exp_date < datetime.now(exp_date.tzinfo)
        except Exception:
            return False

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
        if file_size:
            self.validate_file_size(file_size)

        return self.repo.create(
            filename=filename,
            original_filename=original_filename,
            file_extension=file_extension,
            content_type=content_type,
            file_size=file_size,
            file_hash=file_hash,
            blob_url=blob_url,
            description=description,
            category=category,
            tags=tags,
            is_archived=is_archived,
            status=status,
            expiration_date=expiration_date,
            storage_tier=storage_tier,
        )

    def read_all(self) -> list[Attachment]:
        """
        Read all attachments.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Attachment]:
        """
        Read an attachment by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Attachment]:
        """
        Read an attachment by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_category(self, category: str) -> list[Attachment]:
        """
        Read attachments by category.
        """
        return self.repo.read_by_category(category)

    def read_by_hash(self, file_hash: str) -> Optional[Attachment]:
        """
        Read an attachment by file hash (for deduplication).
        """
        return self.repo.read_by_hash(file_hash)

    def update_by_public_id(self, public_id: str, attachment) -> Optional[Attachment]:
        """
        Update an attachment by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        if hasattr(attachment, "row_version") and attachment.row_version:
            existing.row_version = attachment.row_version

        if hasattr(attachment, "filename") and attachment.filename is not None:
            existing.filename = attachment.filename
        if hasattr(attachment, "original_filename") and attachment.original_filename is not None:
            existing.original_filename = attachment.original_filename
        if hasattr(attachment, "file_extension") and attachment.file_extension is not None:
            existing.file_extension = attachment.file_extension
        if hasattr(attachment, "content_type") and attachment.content_type is not None:
            existing.content_type = attachment.content_type
        if hasattr(attachment, "file_size") and attachment.file_size is not None:
            if attachment.file_size:
                self.validate_file_size(attachment.file_size)
            existing.file_size = attachment.file_size
        if hasattr(attachment, "file_hash") and attachment.file_hash is not None:
            existing.file_hash = attachment.file_hash
        if hasattr(attachment, "blob_url") and attachment.blob_url is not None:
            existing.blob_url = attachment.blob_url
        if hasattr(attachment, "description"):
            existing.description = attachment.description
        if hasattr(attachment, "category"):
            existing.category = attachment.category
        if hasattr(attachment, "tags"):
            existing.tags = attachment.tags
        if hasattr(attachment, "is_archived") and attachment.is_archived is not None:
            existing.is_archived = attachment.is_archived
        if hasattr(attachment, "status"):
            existing.status = attachment.status
        if hasattr(attachment, "expiration_date"):
            existing.expiration_date = attachment.expiration_date
        if hasattr(attachment, "storage_tier") and attachment.storage_tier is not None:
            existing.storage_tier = attachment.storage_tier

        return self.repo.update_by_id(existing)

    def archive(self, public_id: str) -> Optional[Attachment]:
        """
        Archive an attachment (soft delete).
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.is_archived = True
        return self.repo.update_by_id(existing)

    def unarchive(self, public_id: str) -> Optional[Attachment]:
        """
        Unarchive an attachment.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.is_archived = False
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Attachment]:
        """
        Delete an attachment by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None

    def increment_download_count(self, public_id: str) -> Optional[Attachment]:
        """
        Increment download count and update last downloaded datetime.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.increment_download_count(existing.id)
        return None

