# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class UserModuleCreate(BaseModel):
    user_id: int = Field(
        description="The ID of the user."
    )
    module_id: int = Field(
        description="The ID of the module."
    )


class UserModuleUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the user module (base64 encoded)."
    )
    user_id: int = Field(
        description="The ID of the user."
    )
    module_id: int = Field(
        description="The ID of the module."
    )
