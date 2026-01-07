# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboPhysicalAddressCreate(BaseModel):
    qbo_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The QBO ID of the physical address.",
    )
    line1: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The first line of the physical address.",
    )
    line2: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The second line of the physical address.",
    )
    city: Optional[str] = Field(
        default=None,
        max_length=100,
        description="The city of the physical address.",
    )
    country: Optional[str] = Field(
        default=None,
        max_length=100,
        description="The country of the physical address.",
    )
    country_sub_division_code: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The state/province code of the physical address.",
    )
    postal_code: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The postal code of the physical address.",
    )


class QboPhysicalAddressUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the physical address (base64 encoded).",
    )
    qbo_id: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The ID of the QBO physical address.",
    )
    line1: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The first line of the physical address.",
    )
    line2: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The second line of the physical address.",
    )
    city: Optional[str] = Field(
        default=None,
        max_length=100,
        description="The city of the physical address.",
    )
    country: Optional[str] = Field(
        default=None,
        max_length=100,
        description="The country of the physical address.",
    )
    country_sub_division_code: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The state/province code of the physical address.",
    )
    postal_code: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The postal code of the physical address.",
    )


class QboPhysicalAddressSyncRequest(BaseModel):
    access_token: str = Field(
        description="QBO OAuth access token.",
    )
    realm_id: str = Field(
        description="QBO company realm ID.",
    )
    address_id: Optional[str] = Field(
        default=None,
        description="Optional ID to use for the address record (defaults to realm_id if not provided).",
    )
