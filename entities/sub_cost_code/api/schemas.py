# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class SubCostCodeCreate(BaseModel):
    number: str = Field(min_length=1, max_length=50, description="The number of the sub cost code.")
    name: str = Field(min_length=1, max_length=255, description="The name of the sub cost code.")
    description: Optional[str] = Field(default=None, max_length=255, description="The description of the sub cost code.")
    cost_code_public_id: str = Field(min_length=1, description="The public ID of the parent cost code.")

class SubCostCodeUpdate(BaseModel):
    row_version: str = Field(description="The row version of the sub cost code (base64 encoded).")
    number: str = Field(min_length=1, max_length=50, description="The number of the sub cost code.")
    name: str = Field(min_length=1, max_length=255, description="The name of the sub cost code.")
    description: Optional[str] = Field(default=None, max_length=255, description="The description of the sub cost code.")
    cost_code_public_id: str = Field(min_length=1, description="The public ID of the parent cost code.")


class SubCostCodeAliasCreate(BaseModel):
    sub_cost_code_id: int = Field(gt=0, description="The ID of the parent sub cost code.")
    alias: str = Field(min_length=1, max_length=255, description="The alias value for matching.")
    source: Optional[str] = Field(default=None, max_length=50, description="Origin of the alias (e.g. 'manual', 'bill_agent').")


class SubCostCodeAliasUpdate(BaseModel):
    row_version: str = Field(description="The row version of the alias (base64 encoded).")
    sub_cost_code_id: int = Field(gt=0, description="The ID of the parent sub cost code.")
    alias: str = Field(min_length=1, max_length=255, description="The alias value for matching.")
    source: Optional[str] = Field(default=None, max_length=50, description="Origin of the alias (e.g. 'manual', 'bill_agent').")

