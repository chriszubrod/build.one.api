# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class AddressTypeCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the address type."
    )
    description: str = Field(
        min_length=1,
        max_length=255,
        description="The description of the address type."
    )
    display_order: int = Field(
        description="The display order of the address type."
    )


class AddressTypeUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the address type (base64 encoded)."
    )
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the address type."
    )
    description: str = Field(
        min_length=1,
        max_length=255,
        description="The description of the address type."
    )
    display_order: int = Field(
        description="The display order of the address type."
    )
