# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class RoleCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=255,
        description="The name of the role."
    )


class RoleUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the role (base64 encoded)."
    )
    name: str = Field(
        min_length=1,
        max_length=255,
        description="The name of the role."
    )
