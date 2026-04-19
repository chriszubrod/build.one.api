# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class TimeEntryCreate(BaseModel):
    """Schema for creating a new time entry."""
    user_public_id: str = Field(
        description="The public ID of the user (worker)."
    )
    work_date: str = Field(
        description="The date of work (YYYY-MM-DD format)."
    )
    note: Optional[str] = Field(
        default=None,
        description="Worker's note for the day."
    )


class TimeEntryUpdate(BaseModel):
    """Schema for updating a time entry."""
    row_version: str = Field(
        description="The row version for optimistic concurrency (base64 encoded)."
    )
    user_public_id: Optional[str] = Field(
        default=None,
        description="The public ID of the user (worker)."
    )
    work_date: Optional[str] = Field(
        default=None,
        description="The date of work (YYYY-MM-DD format)."
    )
    note: Optional[str] = Field(
        default=None,
        description="Worker's note for the day."
    )


class TimeLogCreate(BaseModel):
    """Schema for creating a new time log."""
    clock_in: str = Field(
        description="Clock in timestamp (YYYY-MM-DD HH:MM:SS format)."
    )
    clock_out: Optional[str] = Field(
        default=None,
        description="Clock out timestamp (YYYY-MM-DD HH:MM:SS format). NULL = still clocked in."
    )
    log_type: str = Field(
        default="work",
        description="Log type: 'work' or 'break'."
    )
    latitude: Optional[Decimal] = Field(
        default=None,
        description="GPS latitude at clock in/out."
    )
    longitude: Optional[Decimal] = Field(
        default=None,
        description="GPS longitude at clock in/out."
    )
    project_id: Optional[int] = Field(
        default=None,
        description="FK to Project. Required for submission."
    )
    note: Optional[str] = Field(
        default=None,
        description="Worker's note for this session."
    )


class TimeLogUpdate(BaseModel):
    """Schema for updating a time log."""
    row_version: str = Field(
        description="The row version for optimistic concurrency (base64 encoded)."
    )
    clock_in: Optional[str] = Field(
        default=None,
        description="Clock in timestamp."
    )
    clock_out: Optional[str] = Field(
        default=None,
        description="Clock out timestamp."
    )
    log_type: Optional[str] = Field(
        default=None,
        description="Log type: 'work' or 'break'."
    )
    latitude: Optional[Decimal] = Field(
        default=None,
        description="GPS latitude at clock in/out."
    )
    longitude: Optional[Decimal] = Field(
        default=None,
        description="GPS longitude at clock in/out."
    )
    project_id: Optional[int] = Field(
        default=None,
        description="FK to Project."
    )
    note: Optional[str] = Field(
        default=None,
        description="Worker's note for this session."
    )


class TimeEntryReject(BaseModel):
    """Schema for rejecting a time entry."""
    note: Optional[str] = Field(
        default=None,
        description="Reason for rejection."
    )


class TimeEntryApprove(BaseModel):
    """Schema for approving a time entry."""
    note: Optional[str] = Field(
        default=None,
        description="Approval notes."
    )
