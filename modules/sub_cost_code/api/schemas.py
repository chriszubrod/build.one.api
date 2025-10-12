# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class SubCostCodeCreate(BaseModel):
    cost_code_public_id: str = Field(min_length=1, max_length=36, description="The public ID of the parent cost code.")
    number: str = Field(min_length=1, max_length=50, description="The number of the sub cost code.")
    name: str = Field(min_length=1, max_length=255, description="The name of the sub cost code.")
    description: Optional[str] = Field(default=None, max_length=255, description="The description of the sub cost code.")


class SubCostCodeUpdate(BaseModel):
    row_version: str = Field(description="The row version of the sub cost code (base64 encoded).")
    cost_code_public_id: str = Field(min_length=1, max_length=36, description="The public ID of the parent cost code.")
    number: str = Field(min_length=1, max_length=50, description="The number of the sub cost code.")
    name: str = Field(min_length=1, max_length=255, description="The name of the sub cost code.")
    description: Optional[str] = Field(default=None, max_length=255, description="The description of the sub cost code.")
