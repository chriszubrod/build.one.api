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


class VendorUpdate(BaseModel):
    row_version: str = Field(
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
