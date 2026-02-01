# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class RoleModuleCreate(BaseModel):
    role_id: str = Field(
        description="The ID of the role."
    )
    module_id: str = Field(
        description="The ID of the module."
    )


class RoleModuleUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the role module (base64 encoded)."
    )
    role_id: str = Field(
        description="The ID of the role."
    )
    module_id: str = Field(
        description="The ID of the module."
    )
