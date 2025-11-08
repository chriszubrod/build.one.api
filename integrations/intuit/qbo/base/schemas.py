# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel

# Local Imports


class _QboBaseModel(BaseModel):
    class Config:
        allow_population_by_field_name = True
        anystr_strip_whitespace = True
        extra = "ignore"
