# Python Standard Library Imports
from typing import List, Optional, Any, Dict

# Third-party Imports
from pydantic import BaseModel, Field


class _QboBaseModel(BaseModel):
    """Base model with common configuration for QBO schemas."""

    class Config:
        populate_by_name = True


class QboAttachableRef(BaseModel):
    """
    Reference linking an Attachable to a transaction entity.
    """
    entity_ref_type: Optional[str] = Field(default=None, alias="EntityRef", description="Type of entity (Bill, Invoice, etc.)")
    entity_ref_value: Optional[str] = Field(default=None, description="ID of the referenced entity")
    include_on_send: Optional[bool] = Field(default=None, alias="IncludeOnSend")
    line_info: Optional[str] = Field(default=None, alias="LineInfo")
    no_ref_only: Optional[bool] = Field(default=None, alias="NoRefOnly")
    inactive: Optional[bool] = Field(default=None, alias="Inactive")
    custom_field: Optional[List[Dict[str, Any]]] = Field(default=None, alias="CustomField")

    class Config:
        populate_by_name = True

    def __init__(self, **data):
        # Handle nested EntityRef object
        if "EntityRef" in data and isinstance(data["EntityRef"], dict):
            entity_ref = data.pop("EntityRef")
            data["entity_ref_type"] = entity_ref.get("type")
            data["entity_ref_value"] = entity_ref.get("value")
        super().__init__(**data)


class QboAttachable(_QboBaseModel):
    """
    Attachable entity from QBO API.
    Represents a file attachment that can be linked to transactions.
    """
    id: Optional[str] = Field(default=None, alias="Id")
    sync_token: Optional[str] = Field(default=None, alias="SyncToken")
    
    # File metadata
    file_name: Optional[str] = Field(default=None, alias="FileName")
    note: Optional[str] = Field(default=None, alias="Note")
    category: Optional[str] = Field(default=None, alias="Category")
    content_type: Optional[str] = Field(default=None, alias="ContentType")
    place_name: Optional[str] = Field(default=None, alias="PlaceName")
    
    # File location
    file_access_uri: Optional[str] = Field(default=None, alias="FileAccessUri")
    temp_download_uri: Optional[str] = Field(default=None, alias="TempDownloadUri")
    size: Optional[int] = Field(default=None, alias="Size")
    
    # Thumbnail
    thumbnail_file_access_uri: Optional[str] = Field(default=None, alias="ThumbnailFileAccessUri")
    thumbnail_temp_download_uri: Optional[str] = Field(default=None, alias="ThumbnailTempDownloadUri")
    
    # Geographic info (for photos)
    lat: Optional[str] = Field(default=None, alias="Lat")
    long: Optional[str] = Field(default=None, alias="Long")
    
    # Tags
    tag: Optional[str] = Field(default=None, alias="Tag")
    
    # References to linked entities
    attachable_ref: Optional[List[QboAttachableRef]] = Field(default_factory=list, alias="AttachableRef")
    
    # Metadata
    meta_data: Optional[Dict[str, Any]] = Field(default=None, alias="MetaData")


class QboAttachableResponse(_QboBaseModel):
    """Response wrapper for single Attachable."""
    attachable: Optional[QboAttachable] = Field(default=None, alias="Attachable")


class QboAttachableQueryResponse(_QboBaseModel):
    """Response wrapper for Attachable query."""
    query_response: Optional[Dict[str, Any]] = Field(default=None, alias="QueryResponse")

    def get_attachables(self) -> List[QboAttachable]:
        """Extract Attachables from query response."""
        if not self.query_response:
            return []
        attachables_data = self.query_response.get("Attachable", [])
        return [QboAttachable(**a) for a in attachables_data]
