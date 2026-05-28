# Python Standard Library Imports
from decimal import Decimal
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field


class VendorProjectRateCreate(BaseModel):
    vendor_public_id: str = Field(..., description="PublicId of the Vendor.")
    project_public_id: str = Field(..., description="PublicId of the Project.")
    hourly_rate: Optional[Decimal] = Field(default=None, description="Override rate; NULL inherits Vendor default.")
    markup: Optional[Decimal] = Field(default=None, description="Override markup; NULL inherits Vendor default.")
    notes: Optional[str] = Field(default=None)


class VendorProjectRateUpdate(BaseModel):
    row_version: Optional[str] = Field(default=None)
    hourly_rate: Optional[Decimal] = Field(default=None)
    markup: Optional[Decimal] = Field(default=None)
    notes: Optional[str] = Field(default=None)
