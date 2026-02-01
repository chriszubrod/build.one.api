# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ProjectAddressCreate(BaseModel):
    project_id: int = Field(
        description="The ID of the project."
    )
    address_id: int = Field(
        description="The ID of the address."
    )
    address_type_id: int = Field(
        description="The ID of the address type."
    )


class ProjectAddressUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the project address (base64 encoded)."
    )
    project_id: int = Field(
        description="The ID of the project."
    )
    address_id: int = Field(
        description="The ID of the address."
    )
    address_type_id: int = Field(
        description="The ID of the address type."
    )
