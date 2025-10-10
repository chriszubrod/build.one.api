# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255, description="The name of the organization.")
    website: Optional[str] = Field(max_length=255, description="The website of the organization.")


class OrganizationUpdate(BaseModel):
    row_version: str = Field(description="The row version of the organization (base64 encoded).")
    name: str = Field(min_length=1, max_length=255, description="The name of the organization.")
    website: Optional[str] = Field(max_length=255, description="The website of the organization.")
