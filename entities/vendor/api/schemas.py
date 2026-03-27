# Python Standard Library Imports
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


class VendorCreate(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=450,
        description="The name of the vendor.",
    )
    abbreviation: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The abbreviation used to identify the vendor.",
    )
    taxpayer_public_id: Optional[str] = Field(
        default=None,
        description="The taxpayer public ID associated with the vendor.",
    )
    vendor_type_public_id: Optional[str] = Field(
        default=None,
        description="The vendor type public ID associated with the vendor type.",
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the vendor is a draft (incomplete).",
    )
    is_contract_labor: Optional[bool] = Field(
        default=False,
        description="Whether the vendor is eligible for contract labor records.",
    )

    @field_validator('name', mode='before')
    @classmethod
    def sanitize_name(cls, v):
        return _sanitize_string(v)

    @field_validator('abbreviation', mode='before')
    @classmethod
    def sanitize_abbreviation(cls, v):
        return _sanitize_string(v)


class VendorUpdate(BaseModel):
    row_version: Optional[str] = Field(
        default=None,
        description="The row version of the vendor (base64 encoded).",
    )
    name: Optional[str] = Field(
        default=None,
        max_length=450,
        description="The name of the vendor.",
    )
    abbreviation: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The abbreviation used to identify the vendor.",
    )
    taxpayer_public_id: Optional[str] = Field(
        default=None,
        description="The taxpayer public ID associated with the taxpayer record.",
    )
    vendor_type_public_id: Optional[str] = Field(
        default=None,
        description="The vendor type public ID associated with the vendor type record.",
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the vendor record is a draft (incomplete).",
    )
    is_contract_labor: Optional[bool] = Field(
        default=None,
        description="Whether the vendor is eligible for contract labor records.",
    )

    @field_validator('name', mode='before')
    @classmethod
    def sanitize_name(cls, v):
        return _sanitize_string(v)

    @field_validator('abbreviation', mode='before')
    @classmethod
    def sanitize_abbreviation(cls, v):
        return _sanitize_string(v)
