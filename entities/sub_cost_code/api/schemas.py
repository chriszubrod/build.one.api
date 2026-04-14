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
    aliases: Optional[str] = Field(default=None, max_length=500, description="Pipe-delimited alias values for matching (e.g. '01.1|1.1').")

class SubCostCodeUpdate(BaseModel):
    row_version: str = Field(description="The row version of the sub cost code (base64 encoded).")
    number: str = Field(min_length=1, max_length=50, description="The number of the sub cost code.")
    name: str = Field(min_length=1, max_length=255, description="The name of the sub cost code.")
    description: Optional[str] = Field(default=None, max_length=255, description="The description of the sub cost code.")
    cost_code_public_id: str = Field(min_length=1, description="The public ID of the parent cost code.")
    aliases: Optional[str] = Field(default=None, max_length=500, description="Pipe-delimited alias values for matching (e.g. '01.1|1.1').")
