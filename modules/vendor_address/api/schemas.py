# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class VendorAddressCreate(BaseModel):
    vendor_id: str = Field(
        min_length=1,
        description="The ID of the vendor."
    )
    address_id: str = Field(
        min_length=1,
        description="The ID of the address."
    )
    address_type_id: str = Field(
        min_length=1,
        description="The ID of the address type."
    )


class VendorAddressUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the vendor address (base64 encoded)."
    )
    vendor_id: str = Field(
        min_length=1,
        description="The ID of the vendor."
    )
    address_id: str = Field(
        min_length=1,
        description="The ID of the address."
    )
    address_type_id: str = Field(
        min_length=1,
        description="The ID of the address type."
    )
