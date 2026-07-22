# Python Standard Library Imports
from typing import Literal

# Third-party Imports
from pydantic import BaseModel

# Local Imports


class VendorTypeRequiredCoverageCreate(BaseModel):
    vendor_type_id: int
    coverage_type: Literal["GL", "WC"]
