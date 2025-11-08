# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class MapVendorQboVendorCreate(BaseModel):
    vendor_id: Optional[str] = Field(
        default=None,
        description="The ID of the vendor.",
    )
    qbo_vendor_id: Optional[str] = Field(
        default=None,
        description="The ID of the QBO vendor.",
    )

class MapVendorQboVendorUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the map vendor qbo vendor mapping (base64 encoded).",
    )
    vendor_id: Optional[str] = Field(
        default=None,
        description="The ID of the vendor.",
    )
    qbo_vendor_id: Optional[str] = Field(
        default=None,
        description="The ID of the QBO vendor.",
    )
