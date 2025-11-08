# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class VendorTypeCreate(BaseModel):
    name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The name of the vendor type."
    )
    description: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The description of the vendor type."
    )


class VendorTypeUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the vendor type (base64 encoded)."
    )
    name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The name of the vendor type."
    )
    description: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The description of the vendor type."
    )
