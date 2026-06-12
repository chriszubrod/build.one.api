# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field


class BudgetRevisionCreate(BaseModel):
    budget_public_id: str = Field(..., description="PublicId of the parent Budget.")
    type: Optional[str] = Field(
        default="change_order",
        description=(
            "'change_order' (default; requires an active budget). 'original' is "
            "reserved for the internal Rev 0 created by Budget create."
        ),
    )
    title: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None)
    effective_date: Optional[str] = Field(default=None, description="ISO date string (YYYY-MM-DD).")


class BudgetRevisionUpdate(BaseModel):
    """Title / Description / EffectiveDate are set UNCONDITIONALLY (clearable)
    — always send the full row; omitted fields are cleared."""

    row_version: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None)
    effective_date: Optional[str] = Field(default=None, description="ISO date string (YYYY-MM-DD).")


class BudgetRevisionApprove(BaseModel):
    row_version: str = Field(
        ...,
        description="Base64 rowversion of the revision being approved (required — prevents stale-read approvals).",
    )
