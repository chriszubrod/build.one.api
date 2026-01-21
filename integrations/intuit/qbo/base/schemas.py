# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, ConfigDict

# Local Imports


class _QboBaseModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="ignore",
    )
