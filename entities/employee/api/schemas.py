# Python Standard Library Imports
from decimal import Decimal
from typing import Optional
import re

# Third-party Imports
from pydantic import BaseModel, Field, field_validator

# Local Imports


def _sanitize_string(value: Optional[str]) -> Optional[str]:
    """Strip whitespace and collapse internal whitespace for string fields."""
    if value is None:
        return None
    value = value.strip()
    value = re.sub(r'\s+', ' ', value)
    return value if value else None


class EmployeeCreate(BaseModel):
    firstname: str = Field(..., min_length=1, max_length=50)
    lastname: str = Field(..., min_length=1, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)
    hourly_rate: Optional[Decimal] = Field(default=None, description="Hourly rate; persisted as Decimal — never float.")
    markup: Optional[Decimal] = Field(default=None, description="Markup as decimal (e.g. 0.50 = 50%).")
    is_active: Optional[bool] = Field(default=True)
    notes: Optional[str] = Field(default=None)

    @field_validator('firstname', mode='before')
    @classmethod
    def sanitize_firstname(cls, v):
        return _sanitize_string(v)

    @field_validator('lastname', mode='before')
    @classmethod
    def sanitize_lastname(cls, v):
        return _sanitize_string(v)


class EmployeeUpdate(BaseModel):
    row_version: Optional[str] = Field(default=None, description="Base64-encoded row version (optimistic concurrency).")
    firstname: Optional[str] = Field(default=None, max_length=50)
    lastname: Optional[str] = Field(default=None, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)
    hourly_rate: Optional[Decimal] = Field(default=None)
    markup: Optional[Decimal] = Field(default=None)
    is_active: Optional[bool] = Field(default=None)
    notes: Optional[str] = Field(default=None)

    @field_validator('firstname', mode='before')
    @classmethod
    def sanitize_firstname(cls, v):
        return _sanitize_string(v)

    @field_validator('lastname', mode='before')
    @classmethod
    def sanitize_lastname(cls, v):
        return _sanitize_string(v)
