# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class UserCreate(BaseModel):
    firstname: str = Field(
        min_length=1,
        max_length=50,
        description="The firstname of the user."
    )
    lastname: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The lastname of the user."
    )


class UserUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the user (base64 encoded)."
    )
    firstname: str = Field(
        min_length=1,
        max_length=50,
        description="The firstname of the user."
    )
    lastname: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The lastname of the user."
    )
