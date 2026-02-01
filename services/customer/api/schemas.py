# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class CustomerCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the customer."
    )
    email: str = Field(
        min_length=1,
        max_length=255,
        description="The email of the customer."
    )
    phone: str = Field(
        min_length=1,
        max_length=50,
        description="The phone of the customer."
    )


class CustomerUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the customer (base64 encoded)."
    )
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the customer."
    )
    email: str = Field(
        min_length=1,
        max_length=255,
        description="The email of the customer."
    )
    phone: str = Field(
        min_length=1,
        max_length=50,
        description="The phone of the customer."
    )
