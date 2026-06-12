# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field


class BudgetCreate(BaseModel):
    project_public_id: str = Field(..., description="PublicId of the Project this budget belongs to.")
    notes: Optional[str] = Field(default=None)


class BudgetUpdate(BaseModel):
    """Notes only — Status never changes via update (use /activate/budget).

    notes=None preserves the existing value; notes='' clears it.
    """
    row_version: Optional[str] = Field(default=None, description="Base64 ROWVERSION for optimistic concurrency.")
    notes: Optional[str] = Field(default=None)


class BudgetActivate(BaseModel):
    row_version: str = Field(..., description="Base64 ROWVERSION — required; body-less activate has a stale-read race.")
