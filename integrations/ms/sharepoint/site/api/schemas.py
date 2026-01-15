# Python Standard Library Imports
from typing import Optional, List

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class SiteSearchRequest(BaseModel):
    """Request model for searching SharePoint sites."""
    query: str = Field(
        min_length=1,
        max_length=255,
        description="Search query for SharePoint sites"
    )


class SiteLinkRequest(BaseModel):
    """Request model for linking a SharePoint site."""
    site_id: str = Field(
        min_length=1,
        max_length=255,
        description="The MS Graph site ID to link"
    )


class SiteUpdateRequest(BaseModel):
    """Request model for updating a linked site."""
    display_name: str = Field(
        min_length=1,
        max_length=255,
        description="The display name for the site"
    )


class SiteResponse(BaseModel):
    """Response model for a SharePoint site."""
    site_id: Optional[str] = Field(
        default=None,
        description="The MS Graph site ID"
    )
    display_name: Optional[str] = Field(
        default=None,
        description="The display name of the site"
    )
    web_url: Optional[str] = Field(
        default=None,
        description="The web URL of the site"
    )
    hostname: Optional[str] = Field(
        default=None,
        description="The hostname of the site"
    )


class LinkedSiteResponse(BaseModel):
    """Response model for a linked SharePoint site (stored in database)."""
    id: Optional[int] = Field(
        default=None,
        description="The internal database ID"
    )
    public_id: Optional[str] = Field(
        default=None,
        description="The public UUID of the linked site"
    )
    site_id: Optional[str] = Field(
        default=None,
        description="The MS Graph site ID"
    )
    display_name: Optional[str] = Field(
        default=None,
        description="The display name of the site"
    )
    web_url: Optional[str] = Field(
        default=None,
        description="The web URL of the site"
    )
    hostname: Optional[str] = Field(
        default=None,
        description="The hostname of the site"
    )
    created_datetime: Optional[str] = Field(
        default=None,
        description="When the site was linked"
    )
    modified_datetime: Optional[str] = Field(
        default=None,
        description="When the linked site was last modified"
    )


class SiteSearchResponse(BaseModel):
    """Response model for site search results."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    sites: List[SiteResponse] = Field(
        default=[],
        description="List of matching sites"
    )


class SiteLinkResponse(BaseModel):
    """Response model for site link operation."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    site: Optional[dict] = Field(
        default=None,
        description="The linked site data"
    )
