# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class UserOrganizationCreate(BaseModel):
    user_id: int = Field(description="The ID of the user.")
    organization_id: int = Field(description="The ID of the organization.")


class UserOrganizationUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the user organization (base64 encoded)."
    )
    user_id: int = Field(description="The ID of the user.")
    organization_id: int = Field(description="The ID of the organization.")
