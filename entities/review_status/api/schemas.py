# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ReviewStatusCreate(BaseModel):
    name: str = Field(
        max_length=100,
        description="The name of the review status."
    )
    description: Optional[str] = Field(
        default=None,
        max_length=500,
        description="The description of the review status."
    )
    sort_order: int = Field(
        default=0,
        description="The sort order for status progression."
    )
    is_final: bool = Field(
        default=False,
        description="Whether this is a final (terminal) status."
    )
    is_declined: bool = Field(
        default=False,
        description="Whether this is a declined/rejected status."
    )
    is_active: bool = Field(
        default=True,
        description="Whether this status is active and available for use."
    )
    is_initial: bool = Field(
        default=False,
        description=(
                    "Whether this is the INITIAL status every submission is created at. "
                    "Exactly one active status must carry it, and it may not "
                    "also be final or declined. Before U-444 this was inferred "
                    "from MIN(sort_order), which made reordering retroactively "
                    "relabel historical reviews."
        )
    )
    color: Optional[str] = Field(
        default=None,
        max_length=7,
        description="Hex color code for UI badge display (e.g., '#4CAF50')."
    )


class ReviewStatusUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the review status (base64 encoded)."
    )
    name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="The name of the review status."
    )
    description: Optional[str] = Field(
        default=None,
        max_length=500,
        description="The description of the review status."
    )
    sort_order: Optional[int] = Field(
        default=None,
        description="The sort order for status progression."
    )
    is_final: Optional[bool] = Field(
        default=None,
        description="Whether this is a final (terminal) status."
    )
    is_declined: Optional[bool] = Field(
        default=None,
        description="Whether this is a declined/rejected status."
    )
    is_active: Optional[bool] = Field(
        default=None,
        description="Whether this status is active and available for use."
    )
    is_initial: Optional[bool] = Field(
        default=None,
        description=(
                    "Whether this is the INITIAL status every submission is created at. "
                    "Exactly one active status must carry it, and it may not "
                    "also be final or declined. Before U-444 this was inferred "
                    "from MIN(sort_order), which made reordering retroactively "
                    "relabel historical reviews."
        )
    )
    color: Optional[str] = Field(
        default=None,
        max_length=7,
        description="Hex color code for UI badge display (e.g., '#4CAF50')."
    )
