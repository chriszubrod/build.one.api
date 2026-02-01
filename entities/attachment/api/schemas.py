# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class AttachmentCreate(BaseModel):
    filename: Optional[str] = Field(
        default=None,
        description="The current filename of the attachment.",
    )
    original_filename: Optional[str] = Field(
        default=None,
        description="The original filename when uploaded.",
    )
    file_extension: Optional[str] = Field(
        default=None,
        max_length=10,
        description="The file extension.",
    )
    content_type: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The MIME content type of the file.",
    )
    file_size: Optional[int] = Field(
        default=None,
        description="The file size in bytes.",
    )
    file_hash: Optional[str] = Field(
        default=None,
        max_length=64,
        description="The SHA-256 hash of the file content.",
    )
    blob_url: Optional[str] = Field(
        default=None,
        description="The Azure Blob Storage URL.",
    )
    description: Optional[str] = Field(
        default=None,
        description="User description of the attachment.",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The category/classification of the attachment.",
    )
    tags: Optional[str] = Field(
        default=None,
        description="Comma-separated or JSON tags.",
    )
    is_archived: Optional[bool] = Field(
        default=False,
        description="Whether the attachment is archived.",
    )
    status: Optional[str] = Field(
        default=None,
        max_length=20,
        description="The workflow status (Draft, Approved, Rejected, Pending).",
    )
    expiration_date: Optional[str] = Field(
        default=None,
        description="The expiration date of the document (ISO 8601 format).",
    )
    storage_tier: Optional[str] = Field(
        default="Hot",
        max_length=20,
        description="The Azure Blob Storage tier (Hot, Cool, Archive).",
    )


class AttachmentUpdate(BaseModel):
    row_version: Optional[str] = Field(
        default=None,
        description="The row version of the attachment (base64 encoded).",
    )
    filename: Optional[str] = Field(
        default=None,
        description="The current filename of the attachment.",
    )
    original_filename: Optional[str] = Field(
        default=None,
        description="The original filename when uploaded.",
    )
    file_extension: Optional[str] = Field(
        default=None,
        max_length=10,
        description="The file extension.",
    )
    content_type: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The MIME content type of the file.",
    )
    file_size: Optional[int] = Field(
        default=None,
        description="The file size in bytes.",
    )
    file_hash: Optional[str] = Field(
        default=None,
        max_length=64,
        description="The SHA-256 hash of the file content.",
    )
    blob_url: Optional[str] = Field(
        default=None,
        description="The Azure Blob Storage URL.",
    )
    description: Optional[str] = Field(
        default=None,
        description="User description of the attachment.",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The category/classification of the attachment.",
    )
    tags: Optional[str] = Field(
        default=None,
        description="Comma-separated or JSON tags.",
    )
    is_archived: Optional[bool] = Field(
        default=None,
        description="Whether the attachment is archived.",
    )
    status: Optional[str] = Field(
        default=None,
        max_length=20,
        description="The workflow status (Draft, Approved, Rejected, Pending).",
    )
    expiration_date: Optional[str] = Field(
        default=None,
        description="The expiration date of the document (ISO 8601 format).",
    )
    storage_tier: Optional[str] = Field(
        default=None,
        max_length=20,
        description="The Azure Blob Storage tier (Hot, Cool, Archive).",
    )
