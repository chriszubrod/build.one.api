# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class VendorCreate(BaseModel):
    name: Optional[str] = Field(
        default=None,
        max_length=50,
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


class VendorUpdate(BaseModel):
    row_version: Optional[str] = Field(
        default=None,
        description="The row version of the vendor (base64 encoded).",
    )
    name: Optional[str] = Field(
        default=None,
        max_length=50,
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
