# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class UserProjectCreate(BaseModel):
    user_id: int = Field(
        description="The ID of the user."
    )
    project_id: int = Field(
        description="The ID of the project."
    )


class UserProjectUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the user project (base64 encoded)."
    )
    user_id: int = Field(
        description="The ID of the user."
    )
    project_id: int = Field(
        description="The ID of the project."
    )
