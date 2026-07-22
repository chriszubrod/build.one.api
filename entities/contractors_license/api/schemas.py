# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ContractorsLicenseCreate(BaseModel):
    vendor_public_id: str = Field(
        min_length=1,
        description="The public ID of the vendor.",
    )
    license_number: Optional[str] = Field(
        default=None,
        description="The license number.",
    )
    issuing_authority: Optional[str] = Field(
        default=None,
        description="The issuing authority.",
    )
    classification: Optional[str] = Field(
        default=None,
        description="The license classification.",
    )
    issue_date: Optional[str] = Field(
        default=None,
        description="The issue date.",
    )
    expiry_date: Optional[str] = Field(
        default=None,
        description="The expiry date.",
    )
    verification_status: Optional[str] = Field(
        default="Received",
        description="Verification status of the license.",
    )


class ContractorsLicenseUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the contractors license (base64 encoded).",
    )
    license_number: Optional[str] = Field(
        default=None,
        description="The license number.",
    )
    issuing_authority: Optional[str] = Field(
        default=None,
        description="The issuing authority.",
    )
    classification: Optional[str] = Field(
        default=None,
        description="The license classification.",
    )
    issue_date: Optional[str] = Field(
        default=None,
        description="The issue date.",
    )
    expiry_date: Optional[str] = Field(
        default=None,
        description="The expiry date.",
    )
    verification_status: Optional[str] = Field(
        default=None,
        description="Verification status of the license.",
    )
