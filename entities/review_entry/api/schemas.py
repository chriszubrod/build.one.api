# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ReviewEntrySubmit(BaseModel):
    """Submit a bill for review (creates first entry with initial status)."""
    bill_public_id: str = Field(description="Public ID of the bill to submit for review.")
    comments: Optional[str] = Field(default=None, description="Optional comments.")


class ReviewEntryAdvance(BaseModel):
    """Advance to the next status in the review workflow."""
    bill_public_id: str = Field(description="Public ID of the bill.")
    comments: Optional[str] = Field(default=None, description="Optional comments.")


class ReviewEntryDecline(BaseModel):
    """Decline/reject the review."""
    bill_public_id: str = Field(description="Public ID of the bill.")
    review_status_public_id: str = Field(description="Public ID of the declined status to apply.")
    comments: Optional[str] = Field(default=None, description="Optional comments explaining the decline.")


class ReviewEntryCreate(BaseModel):
    """Direct create (for admin/system use via workflow engine)."""
    review_status_public_id: str = Field(description="Public ID of the review status.")
    bill_public_id: Optional[str] = Field(default=None, description="Public ID of the bill.")
    comments: Optional[str] = Field(default=None, description="Optional comments.")


class ReviewEntryUpdate(BaseModel):
    """Update a review entry (only comments are editable)."""
    row_version: str = Field(description="The row version (base64 encoded).")
    comments: Optional[str] = Field(default=None, description="Updated comments.")
