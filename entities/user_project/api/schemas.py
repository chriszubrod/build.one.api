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
    role_public_id: Optional[str] = Field(
        default=None,
        description="The public ID of the Role (e.g., 'Project Manager', 'Owner') the user holds on this project. Optional; NULL = generic project membership.",
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
    role_public_id: Optional[str] = Field(
        default=None,
        description="The public ID of the Role to qualify the user's project membership. Optional.",
    )
