# Python Standard Library Imports
from decimal import Decimal
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field


class EmployeeLaborCreate(BaseModel):
    employee_public_id: str = Field(..., description="PublicId of the Employee.")
    project_public_id: Optional[str] = Field(default=None)
    work_date: str = Field(..., description="ISO date string (YYYY-MM-DD).")
    billing_period_start: str = Field(..., description="ISO date string (1st or 16th).")
    billing_period_end: str = Field(..., description="ISO date string (15th or EOM).")
    total_hours: Optional[Decimal] = Field(default=None)
    hourly_rate: Optional[Decimal] = Field(default=None)
    markup: Optional[Decimal] = Field(default=None)
    total_amount: Optional[Decimal] = Field(default=None)
    sub_cost_code_public_id: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default="pending_review")
    source_time_entry_id: Optional[int] = Field(default=None)


class EmployeeLaborUpdate(BaseModel):
    row_version: Optional[str] = Field(default=None)
    project_public_id: Optional[str] = Field(default=None)
    total_hours: Optional[Decimal] = Field(default=None)
    hourly_rate: Optional[Decimal] = Field(default=None)
    markup: Optional[Decimal] = Field(default=None)
    total_amount: Optional[Decimal] = Field(default=None)
    sub_cost_code_public_id: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    invoice_line_item_id: Optional[int] = Field(default=None)
