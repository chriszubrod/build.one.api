# Python Standard Library Imports
from typing import Optional, List

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class DriveItemLinkRequest(BaseModel):
    """Request model for linking a DriveItem."""
    drive_public_id: str = Field(
        min_length=1,
        max_length=255,
        description="The public ID of the linked drive"
    )
    item_id: str = Field(
        min_length=1,
        max_length=255,
        description="The MS Graph item ID to link"
    )


class FolderCreateRequest(BaseModel):
    """Request model for creating a folder."""
    folder_name: str = Field(
        min_length=1,
        max_length=255,
        description="The name for the new folder"
    )


class DriveItemResponse(BaseModel):
    """Response model for a DriveItem from MS Graph."""
    item_id: Optional[str] = Field(
        default=None,
        description="The MS Graph item ID"
    )
    name: Optional[str] = Field(
        default=None,
        description="The name of the item"
    )
    item_type: Optional[str] = Field(
        default=None,
        description="The type of item (file or folder)"
    )
    size: Optional[int] = Field(
        default=None,
        description="The size in bytes (files only)"
    )
    mime_type: Optional[str] = Field(
        default=None,
        description="The MIME type (files only)"
    )
    web_url: Optional[str] = Field(
        default=None,
        description="The web URL of the item"
    )
    parent_item_id: Optional[str] = Field(
        default=None,
        description="The MS Graph ID of the parent folder"
    )
    graph_created_datetime: Optional[str] = Field(
        default=None,
        description="When the item was created in Graph"
    )
    graph_modified_datetime: Optional[str] = Field(
        default=None,
        description="When the item was last modified in Graph"
    )
    child_count: Optional[int] = Field(
        default=None,
        description="Number of children (folders only)"
    )


class LinkedDriveItemResponse(BaseModel):
    """Response model for a linked DriveItem (stored in database)."""
    id: Optional[int] = Field(
        default=None,
        description="The internal database ID"
    )
    public_id: Optional[str] = Field(
        default=None,
        description="The public UUID of the linked item"
    )
    ms_drive_id: Optional[int] = Field(
        default=None,
        description="The internal database ID of the linked drive"
    )
    item_id: Optional[str] = Field(
        default=None,
        description="The MS Graph item ID"
    )
    parent_item_id: Optional[str] = Field(
        default=None,
        description="The MS Graph ID of the parent folder"
    )
    name: Optional[str] = Field(
        default=None,
        description="The name of the item"
    )
    item_type: Optional[str] = Field(
        default=None,
        description="The type of item (file or folder)"
    )
    size: Optional[int] = Field(
        default=None,
        description="The size in bytes (files only)"
    )
    mime_type: Optional[str] = Field(
        default=None,
        description="The MIME type (files only)"
    )
    web_url: Optional[str] = Field(
        default=None,
        description="The web URL of the item"
    )
    graph_created_datetime: Optional[str] = Field(
        default=None,
        description="When the item was created in Graph"
    )
    graph_modified_datetime: Optional[str] = Field(
        default=None,
        description="When the item was last modified in Graph"
    )
    created_datetime: Optional[str] = Field(
        default=None,
        description="When the item was linked"
    )
    modified_datetime: Optional[str] = Field(
        default=None,
        description="When the linked item was last modified locally"
    )


class DriveItemListResponse(BaseModel):
    """Response model for DriveItem list results."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    items: List[DriveItemResponse] = Field(
        default=[],
        description="List of items"
    )


class DriveItemLinkResponse(BaseModel):
    """Response model for DriveItem link operation."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    item: Optional[dict] = Field(
        default=None,
        description="The linked item data"
    )
