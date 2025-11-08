# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class UserRoleCreate(BaseModel):
    user_id: str = Field(
        description="The ID of the user."
    )
    role_id: str = Field(
        description="The ID of the role."
    )


class UserRoleUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the user role (base64 encoded)."
    )
    user_id: str = Field(
        description="The ID of the user."
    )
    role_id: str = Field(
        description="The ID of the role."
    )
