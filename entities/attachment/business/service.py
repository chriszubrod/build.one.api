# Python Standard Library Imports
import hashlib
import logging
import os
from typing import Optional

# Third-party Imports

# Local Imports
from services.attachment.business.model import Attachment
from services.attachment.persistence.repo import AttachmentRepository

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
        tenant_id: int = None,
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
        # TODO: In Phase 10, use tenant_id for tenant isolation
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

    def read_by_ids(self, ids: list[int]) -> list[Attachment]:
        """
        Read multiple attachments by their IDs in a single query.
        """
        if not ids:
            return []
        return self.repo.read_by_ids(ids)

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

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        filename: str = None,
        original_filename: str = None,
        file_extension: str = None,
        content_type: str = None,
        file_size: int = None,
        file_hash: str = None,
        blob_url: str = None,
        description: str = None,
        category: str = None,
        tags: str = None,
        is_archived: bool = None,
        status: str = None,
        expiration_date: str = None,
        storage_tier: str = None,
    ) -> Optional[Attachment]:
        """
        Update an attachment by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.row_version = row_version

        if filename is not None:
            existing.filename = filename
        if original_filename is not None:
            existing.original_filename = original_filename
        if file_extension is not None:
            existing.file_extension = file_extension
        if content_type is not None:
            existing.content_type = content_type
        if file_size is not None:
            self.validate_file_size(file_size)
            existing.file_size = file_size
        if file_hash is not None:
            existing.file_hash = file_hash
        if blob_url is not None:
            existing.blob_url = blob_url
        if description is not None:
            existing.description = description
        if category is not None:
            existing.category = category
        if tags is not None:
            existing.tags = tags
        if is_archived is not None:
            existing.is_archived = is_archived
        if status is not None:
            existing.status = status
        if expiration_date is not None:
            existing.expiration_date = expiration_date
        if storage_tier is not None:
            existing.storage_tier = storage_tier

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

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Attachment]:
        """
        Delete an attachment by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
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

