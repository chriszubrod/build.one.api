# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class CompanyCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the company."
    )
    website: str = Field(
        min_length=1,
        max_length=255,
        description="The website of the company."
    )


class CompanyUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the company (base64 encoded)."
    )
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the company."
    )
    website: str = Field(
        min_length=1,
        max_length=255,
        description="The website of the company."
    )
