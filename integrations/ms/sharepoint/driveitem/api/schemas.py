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


# DriveItem-Project Connector Schemas

class DriveItemProjectLinkRequest(BaseModel):
    """Request model for linking a driveitem (folder) to a project."""
    project_id: int = Field(
        description="The database ID of the project to link"
    )
    drive_public_id: str = Field(
        min_length=1,
        description="The public ID of the linked drive"
    )
    graph_item_id: str = Field(
        min_length=1,
        description="The MS Graph item ID (folder) to link to the project"
    )


class DriveItemProjectResponse(BaseModel):
    """Response model for driveitem-project link operations."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    mapping: Optional[dict] = Field(
        default=None,
        description="The driveitem-project mapping data"
    )


# DriveItem-Vendor Connector Schemas

class DriveItemVendorLinkRequest(BaseModel):
    """Request model for linking a driveitem (folder) to a vendor."""
    vendor_public_id: str = Field(
        min_length=1,
        description="The public ID of the vendor to link"
    )
    drive_public_id: str = Field(
        min_length=1,
        description="The public ID of the linked drive"
    )
    graph_item_id: str = Field(
        min_length=1,
        description="The MS Graph item ID (folder) to link to the vendor"
    )


# DriveItem-ProjectModule Connector Schemas

class DriveItemProjectModuleLinkRequest(BaseModel):
    """Request model for linking a driveitem (folder) to a project module."""
    project_id: int = Field(
        description="The database ID of the project to link"
    )
    module_public_id: str = Field(
        description="The public ID of the module to link"
    )
    graph_item_id: str = Field(
        min_length=1,
        description="The MS Graph item ID (folder) to link to the project module"
    )


class DriveItemProjectModuleResponse(BaseModel):
    """Response model for driveitem-project-module link operations."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    mapping: Optional[dict] = Field(
        default=None,
        description="The driveitem-project-module mapping data"
    )


# DriveItem-ProjectExcel Connector Schemas

class DriveItemProjectExcelLinkRequest(BaseModel):
    """Request model for linking an Excel workbook to a project."""
    project_id: int = Field(
        description="The database ID of the project to link"
    )
    drive_public_id: str = Field(
        min_length=1,
        description="The public ID of the linked drive"
    )
    graph_item_id: str = Field(
        min_length=1,
        description="The MS Graph item ID (Excel .xlsx file) to link to the project"
    )
    worksheet_name: str = Field(
        min_length=1,
        max_length=255,
        description="The name of the worksheet to target for data operations"
    )


class DriveItemProjectExcelResponse(BaseModel):
    """Response model for driveitem-project-excel link operations."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    mapping: Optional[dict] = Field(
        default=None,
        description="The driveitem-project-excel mapping data"
    )


class DriveItemProjectExcelPushDataRequest(BaseModel):
    """Request model for pushing data to an Excel worksheet."""
    data: List[List] = Field(
        description="2D array of values to push [[row1], [row2], ...]"
    )
    range_address: Optional[str] = Field(
        default=None,
        description="Optional Excel range address (e.g., 'A1:D4'). If not provided, starts at A1."
    )


class DriveItemProjectExcelAppendRowsRequest(BaseModel):
    """Request model for appending rows to an Excel worksheet."""
    rows: List[List] = Field(
        description="2D array of values to append [[row1], [row2], ...]"
    )


class DriveItemProjectExcelClearRangeRequest(BaseModel):
    """Request model for clearing a range in an Excel worksheet."""
    range_address: str = Field(
        min_length=1,
        description="Excel range address to clear (e.g., 'A1:D4')"
    )


# DriveItem-BillFolder Connector Schemas

class DriveItemBillFolderLinkRequest(BaseModel):
    """Request model for linking a driveitem (folder) as a bill processing folder."""
    company_id: int = Field(
        description="The database ID of the company"
    )
    drive_public_id: str = Field(
        min_length=1,
        description="The public ID of the linked drive"
    )
    graph_item_id: str = Field(
        min_length=1,
        description="The MS Graph item ID (folder) to link"
    )
    folder_type: str = Field(
        min_length=1,
        max_length=20,
        description="The folder type: 'source' or 'processed'"
    )


# DriveItem-ExpenseFolder Connector Schemas

class DriveItemExpenseFolderLinkRequest(BaseModel):
    """Request model for linking a driveitem (folder) as an expense processing folder."""
    company_id: int = Field(
        description="The database ID of the company"
    )
    drive_public_id: str = Field(
        min_length=1,
        description="The public ID of the linked drive"
    )
    graph_item_id: str = Field(
        min_length=1,
        description="The MS Graph item ID (folder) to link"
    )
    folder_type: str = Field(
        min_length=1,
        max_length=20,
        description="The folder type: 'source' or 'processed'"
    )
