# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ModuleCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the module."
    )
    route: str = Field(
        min_length=1,
        max_length=255,
        description="The route path for the module."
    )


class ModuleUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the module (base64 encoded)."
    )
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the module."
    )
    route: str = Field(
        min_length=1,
        max_length=255,
        description="The route path for the module."
    )
