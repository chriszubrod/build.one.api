# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class CostCodeCreate(BaseModel):
    number: str = Field(min_length=1, max_length=50, description="The unique number identifier.")
    name: str = Field(min_length=1, max_length=255, description="The name of the cost code.")
    description: Optional[str] = Field(default=None, max_length=255, description="The description of the cost code.")


class CostCodeUpdate(BaseModel):
    row_version: str = Field(description="The row version of the cost code (base64 encoded).")
    number: str = Field(min_length=1, max_length=50, description="The unique number identifier.")
    name: str = Field(min_length=1, max_length=255, description="The name of the cost code.")
    description: Optional[str] = Field(default=None, max_length=255, description="The description of the cost code.")
