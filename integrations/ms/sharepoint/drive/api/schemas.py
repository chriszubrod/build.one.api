# Python Standard Library Imports
from typing import Optional, List

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class DriveLinkRequest(BaseModel):
    """Request model for linking a drive."""
    site_public_id: str = Field(
        min_length=1,
        max_length=255,
        description="The public ID of the linked site"
    )
    drive_id: str = Field(
        min_length=1,
        max_length=255,
        description="The MS Graph drive ID to link"
    )


class DriveUpdateRequest(BaseModel):
    """Request model for updating a linked drive."""
    name: str = Field(
        min_length=1,
        max_length=255,
        description="The name for the drive"
    )


class DriveResponse(BaseModel):
    """Response model for a drive from MS Graph."""
    drive_id: Optional[str] = Field(
        default=None,
        description="The MS Graph drive ID"
    )
    name: Optional[str] = Field(
        default=None,
        description="The name of the drive"
    )
    web_url: Optional[str] = Field(
        default=None,
        description="The web URL of the drive"
    )
    drive_type: Optional[str] = Field(
        default=None,
        description="The type of drive (e.g., documentLibrary)"
    )
    description: Optional[str] = Field(
        default=None,
        description="The description of the drive"
    )
    created_datetime: Optional[str] = Field(
        default=None,
        description="When the drive was created"
    )


class LinkedDriveResponse(BaseModel):
    """Response model for a linked drive (stored in database)."""
    id: Optional[int] = Field(
        default=None,
        description="The internal database ID"
    )
    public_id: Optional[str] = Field(
        default=None,
        description="The public UUID of the linked drive"
    )
    ms_site_id: Optional[int] = Field(
        default=None,
        description="The internal database ID of the linked site"
    )
    drive_id: Optional[str] = Field(
        default=None,
        description="The MS Graph drive ID"
    )
    name: Optional[str] = Field(
        default=None,
        description="The name of the drive"
    )
    web_url: Optional[str] = Field(
        default=None,
        description="The web URL of the drive"
    )
    drive_type: Optional[str] = Field(
        default=None,
        description="The type of drive"
    )
    created_datetime: Optional[str] = Field(
        default=None,
        description="When the drive was linked"
    )
    modified_datetime: Optional[str] = Field(
        default=None,
        description="When the linked drive was last modified"
    )


class DriveListResponse(BaseModel):
    """Response model for drive list results."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    drives: List[DriveResponse] = Field(
        default=[],
        description="List of drives"
    )


class DriveLinkResponse(BaseModel):
    """Response model for drive link operation."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    drive: Optional[dict] = Field(
        default=None,
        description="The linked drive data"
    )
