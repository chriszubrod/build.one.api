# Python Standard Library Imports
from decimal import Decimal
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field


class EmployeeProjectRateCreate(BaseModel):
    employee_public_id: str = Field(..., description="PublicId of the Employee.")
    project_public_id: str = Field(..., description="PublicId of the Project.")
    hourly_rate: Optional[Decimal] = Field(default=None, description="Override rate; NULL inherits Employee default.")
    markup: Optional[Decimal] = Field(default=None, description="Override markup; NULL inherits Employee default.")
    notes: Optional[str] = Field(default=None)


class EmployeeProjectRateUpdate(BaseModel):
    row_version: Optional[str] = Field(default=None)
    hourly_rate: Optional[Decimal] = Field(default=None)
    markup: Optional[Decimal] = Field(default=None)
    notes: Optional[str] = Field(default=None)
