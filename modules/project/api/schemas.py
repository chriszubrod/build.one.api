# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ProjectCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the project."
    )
    description: str = Field(
        min_length=1,
        max_length=500,
        description="The description of the project."
    )
    status: str = Field(
        min_length=1,
        max_length=50,
        description="The status of the project."
    )
    customer_id: Optional[int] = Field(
        default=None,
        description="The ID of the customer associated with the project."
    )


class ProjectUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the project (base64 encoded)."
    )
    name: str = Field(
        min_length=1,
        max_length=50,
        description="The name of the project."
    )
    description: str = Field(
        min_length=1,
        max_length=500,
        description="The description of the project."
    )
    status: str = Field(
        min_length=1,
        max_length=50,
        description="The status of the project."
    )
    customer_id: Optional[int] = Field(
        default=None,
        description="The ID of the customer associated with the project."
    )