# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class AddressCreate(BaseModel):
    street_one: str = Field(
        min_length=1,
        max_length=255,
        description="The first street line of the address."
    )
    street_two: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The second street line of the address."
    )
    city: str = Field(
        min_length=1,
        max_length=100,
        description="The city of the address."
    )
    state: str = Field(
        min_length=1,
        max_length=50,
        description="The state of the address."
    )
    zip: str = Field(
        min_length=1,
        max_length=20,
        description="The zip code of the address."
    )


class AddressUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the address (base64 encoded)."
    )
    street_one: str = Field(
        min_length=1,
        max_length=255,
        description="The first street line of the address."
    )
    street_two: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The second street line of the address."
    )
    city: str = Field(
        min_length=1,
        max_length=100,
        description="The city of the address."
    )
    state: str = Field(
        min_length=1,
        max_length=50,
        description="The state of the address."
    )
    zip: str = Field(
        min_length=1,
        max_length=20,
        description="The zip code of the address."
    )
