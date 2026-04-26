# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ReviewSubmitRequest(BaseModel):
    comments: Optional[str] = Field(
        default=None,
        description="Optional comments to record on the submission entry.",
    )


class ReviewAdvanceRequest(BaseModel):
    comments: Optional[str] = Field(
        default=None,
        description="Optional comments to record on the advance entry.",
    )


class ReviewDeclineRequest(BaseModel):
    target_status_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional public_id of a declined ReviewStatus. Required when more "
            "than one declined status is configured. If omitted and exactly "
            "one declined status exists, that one is used."
        ),
    )
    comments: Optional[str] = Field(
        default=None,
        description="Optional comments to record on the decline entry.",
    )
