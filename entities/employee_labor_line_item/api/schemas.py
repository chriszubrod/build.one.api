# Python Standard Library Imports
from decimal import Decimal
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field


class EmployeeLaborLineItemCreate(BaseModel):
    employee_labor_public_id: str = Field(...)
    line_date: Optional[str] = Field(default=None)
    project_public_id: Optional[str] = Field(default=None)
    sub_cost_code_public_id: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    hours: Optional[Decimal] = Field(default=None)
    rate: Optional[Decimal] = Field(default=None)
    markup: Optional[Decimal] = Field(default=None)
    price: Optional[Decimal] = Field(default=None)
    is_billable: bool = Field(default=True)
    is_overhead: bool = Field(default=False)


class EmployeeLaborLineItemUpdate(BaseModel):
    row_version: Optional[str] = Field(default=None)
    line_date: Optional[str] = Field(default=None)
    project_public_id: Optional[str] = Field(default=None)
    sub_cost_code_public_id: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    hours: Optional[Decimal] = Field(default=None)
    rate: Optional[Decimal] = Field(default=None)
    markup: Optional[Decimal] = Field(default=None)
    price: Optional[Decimal] = Field(default=None)
    is_billable: Optional[bool] = Field(default=None)
    is_overhead: Optional[bool] = Field(default=None)
    invoice_line_item_id: Optional[int] = Field(default=None)
